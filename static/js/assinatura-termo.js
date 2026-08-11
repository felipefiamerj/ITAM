(() => {
  document.addEventListener('DOMContentLoaded', () => {
    const shell = document.querySelector('[data-signature-pad]');
    const form = document.querySelector('[data-signature-form]');
    const hidden = document.getElementById('id_assinatura_data_url');
    if (!shell || !form || !hidden) {
      return;
    }

    const canvas = shell.querySelector('[data-signature-canvas]');
    const clearButton = shell.querySelector('[data-signature-clear]');
    const ctx = canvas.getContext('2d');
    let drawing = false;
    let hasStroke = false;
    let lastPoint = null;

    const resizeCanvas = () => {
      const rect = canvas.getBoundingClientRect();
      const ratio = Math.max(window.devicePixelRatio || 1, 1);
      const image = hasStroke ? canvas.toDataURL('image/png') : '';
      canvas.width = Math.max(1, Math.floor(rect.width * ratio));
      canvas.height = Math.max(1, Math.floor(rect.height * ratio));
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.lineWidth = 2.4;
      ctx.strokeStyle = '#102033';
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, rect.width, rect.height);

      if (image) {
        const previous = new Image();
        previous.onload = () => {
          ctx.drawImage(previous, 0, 0, rect.width, rect.height);
          hidden.value = canvas.toDataURL('image/png');
        };
        previous.src = image;
      }
    };

    const pointFromEvent = (event) => {
      const rect = canvas.getBoundingClientRect();
      return {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      };
    };

    const begin = (event) => {
      event.preventDefault();
      drawing = true;
      lastPoint = pointFromEvent(event);
      canvas.setPointerCapture(event.pointerId);
    };

    const draw = (event) => {
      if (!drawing || !lastPoint) {
        return;
      }
      event.preventDefault();
      const point = pointFromEvent(event);
      ctx.beginPath();
      ctx.moveTo(lastPoint.x, lastPoint.y);
      ctx.lineTo(point.x, point.y);
      ctx.stroke();
      lastPoint = point;
      hasStroke = true;
      hidden.value = canvas.toDataURL('image/png');
    };

    const end = (event) => {
      if (!drawing) {
        return;
      }
      event.preventDefault();
      drawing = false;
      lastPoint = null;
      hidden.value = hasStroke ? canvas.toDataURL('image/png') : '';
    };

    const clear = () => {
      hasStroke = false;
      hidden.value = '';
      resizeCanvas();
    };

    canvas.addEventListener('pointerdown', begin);
    canvas.addEventListener('pointermove', draw);
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointercancel', end);
    canvas.addEventListener('pointerleave', end);
    clearButton?.addEventListener('click', clear);

    form.addEventListener('submit', (event) => {
      if (!hasStroke) {
        event.preventDefault();
        canvas.focus();
        shell.classList.add('signature-pad-shell-warning');
        window.setTimeout(() => shell.classList.remove('signature-pad-shell-warning'), 1400);
      }
    });

    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);
  });
})();
