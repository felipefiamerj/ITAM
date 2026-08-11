(() => {
  const body = document.body;

  if (!body) {
    return;
  }

  const searchUrl = body.dataset.searchUrl || '/dashboard/busca/';
  const notificationsWsPath = body.dataset.notificationsWsPath || '/ws/notifications/';
  const userId = body.dataset.userId || '';
  const notificationBadge = document.querySelector('[data-notification-badge]');
  const notificationBadgeValue = document.querySelector('[data-notification-badge-value]');
  const notificationToastStack = document.querySelector('[data-notification-toast-stack]');
  const dashboardLiveIndicator = document.querySelector('[data-dashboard-live-indicator]');
  const dashboardLiveText = document.querySelector('[data-dashboard-live-text]');
  const supportsWebSocket = typeof WebSocket !== 'undefined';

  let notificationSocket = null;
  let reconnectTimer = null;
  let manuallyClosed = false;
  let connectionToastShown = false;
  let unreadCount = readInitialUnreadCount();

  const clampPercent = (value) => {
    const parsed = Number.parseFloat(String(value || '').replace(',', '.'));
    if (!Number.isFinite(parsed)) {
      return 0;
    }

    return Math.min(100, Math.max(0, parsed));
  };

  const applyProgressValues = (root = document) => {
    root.querySelectorAll('[data-progress-value]').forEach((element) => {
      const percent = clampPercent(element.dataset.progressValue);
      element.style.width = `${percent}%`;

      const progressbar = element.closest('[role="progressbar"]');
      if (progressbar) {
        progressbar.setAttribute('aria-valuenow', String(Math.round(percent)));
      }
    });
  };

  window.ItamUI = {
    ...(window.ItamUI || {}),
    applyProgressValues,
  };

  const focusSearchInput = () => {
    const input = document.querySelector('[data-search-input]');

    if (body.dataset.searchPage === 'true' && input) {
      input.focus();
      input.select();
      return true;
    }

    return false;
  };

  function readInitialUnreadCount() {
    if (!notificationBadge) {
      return 0;
    }

    const rawCount =
      notificationBadge.dataset.notificationCount ||
      notificationBadgeValue?.textContent ||
      notificationBadge.textContent ||
      '0';
    const parsed = Number.parseInt(rawCount, 10);
    return Number.isNaN(parsed) ? 0 : parsed;
  }

  function syncBadge(count) {
    unreadCount = Math.max(0, Number(count) || 0);

    if (!notificationBadge) {
      return;
    }

    if (notificationBadgeValue) {
      notificationBadgeValue.textContent = String(unreadCount);
    } else {
      notificationBadge.textContent = String(unreadCount);
    }

    notificationBadge.dataset.notificationCount = String(unreadCount);

    if (unreadCount > 0) {
      notificationBadge.classList.remove('d-none');
      notificationBadge.hidden = false;
    } else {
      notificationBadge.classList.add('d-none');
      notificationBadge.hidden = true;
    }
  }

  function setDashboardLiveState(state) {
    if (!dashboardLiveIndicator || !dashboardLiveText) {
      return;
    }

    dashboardLiveIndicator.classList.toggle('is-connected', state === 'connected');
    dashboardLiveIndicator.classList.toggle('is-reconnecting', state === 'reconnecting');

    if (state === 'connected') {
      dashboardLiveText.textContent = 'Tempo real conectado';
    } else if (state === 'reconnecting') {
      dashboardLiveText.textContent = 'Reconectando...';
    } else {
      dashboardLiveText.textContent = 'Atualizado agora';
    }
  }

  function buildToast(payload) {
    if (!notificationToastStack) {
      return null;
    }

    const notification = payload.notification || payload;
    const title = notification.title || 'Nova notificação';
    const message = notification.message || '';
    const link = notification.link || '';
    const toast = document.createElement('article');
    toast.className = 'notification-toast';

    const head = document.createElement('div');
    head.className = 'notification-toast__head';

    const content = document.createElement('div');
    content.className = 'notification-toast__content';

    const toastTitle = document.createElement('strong');
    toastTitle.className = 'notification-toast__title';
    toastTitle.textContent = title;
    content.appendChild(toastTitle);

    if (message) {
      const toastMessage = document.createElement('div');
      toastMessage.className = 'notification-toast__message';
      toastMessage.textContent = message;
      content.appendChild(toastMessage);
    }

    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'notification-toast__close';
    closeButton.setAttribute('aria-label', 'Fechar notificação');
    closeButton.innerHTML = '<i class="fa-solid fa-xmark"></i>';

    const removeToast = () => {
      toast.classList.remove('is-visible');
      window.setTimeout(() => {
        toast.remove();
      }, 240);
    };

    closeButton.addEventListener('click', removeToast);

    head.appendChild(content);
    head.appendChild(closeButton);
    toast.appendChild(head);

    if (link) {
      const actions = document.createElement('div');
      actions.className = 'notification-toast__actions';

      const openLink = document.createElement('a');
      openLink.className = 'notification-toast__link';
      openLink.href = link;
      openLink.textContent = 'Abrir';
      actions.appendChild(openLink);

      toast.appendChild(actions);
    }

    notificationToastStack.appendChild(toast);
    requestAnimationFrame(() => {
      toast.classList.add('is-visible');
    });

    window.setTimeout(removeToast, 7000);
    return toast;
  }

  function showConnectionToast() {
    if (connectionToastShown) {
      return;
    }

    connectionToastShown = true;
    buildToast({
      title: 'Tempo real ativo',
      message: 'As notificações do sistema vão aparecer sem recarregar a página.',
    });
  }

  function handleNotificationEvent(payload) {
    if (!payload || !payload.event) {
      return;
    }

    if (payload.event === 'notifications.sync' || payload.event === 'notifications.state') {
      if (typeof payload.unread_count === 'number') {
        syncBadge(payload.unread_count);
      }

      if (payload.event === 'notifications.sync') {
        setDashboardLiveState('connected');
      }

      return;
    }

    if (payload.event === 'notification.created') {
      if (typeof payload.unread_count === 'number') {
        syncBadge(payload.unread_count);
      } else {
        syncBadge(unreadCount + 1);
      }

      buildToast(payload);
    }
  }

  function connectNotifications() {
    if (
      !supportsWebSocket ||
      !userId ||
      (notificationSocket && (notificationSocket.readyState === WebSocket.OPEN || notificationSocket.readyState === WebSocket.CONNECTING))
    ) {
      return;
    }

    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const socketUrl = `${scheme}://${window.location.host}${notificationsWsPath}`;

    manuallyClosed = false;
    notificationSocket = new WebSocket(socketUrl);

    notificationSocket.addEventListener('open', () => {
      window.clearTimeout(reconnectTimer);
      setDashboardLiveState('connected');
      showConnectionToast();
    });

    notificationSocket.addEventListener('message', (event) => {
      try {
        const payload = JSON.parse(event.data);
        handleNotificationEvent(payload);
      } catch (error) {
        console.error('Falha ao processar notificação em tempo real.', error);
      }
    });

    notificationSocket.addEventListener('close', () => {
      notificationSocket = null;

      if (manuallyClosed) {
        setDashboardLiveState('idle');
        return;
      }

      setDashboardLiveState('reconnecting');
      reconnectTimer = window.setTimeout(connectNotifications, 5000);
    });

    notificationSocket.addEventListener('error', () => {
      setDashboardLiveState('reconnecting');
    });
  }

  if (notificationBadge) {
    syncBadge(unreadCount);
  }

  applyProgressValues();
  connectNotifications();

  document.addEventListener('submit', (event) => {
    const form = event.target.closest('form[data-confirm-message]');
    const message = form?.dataset.confirmMessage;

    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();

      if (!focusSearchInput() && searchUrl) {
        window.location.href = searchUrl;
      }
    }
  });

  window.addEventListener('beforeunload', () => {
    manuallyClosed = true;
    window.clearTimeout(reconnectTimer);

    if (notificationSocket) {
      notificationSocket.close(1000);
    }
  });
})();
