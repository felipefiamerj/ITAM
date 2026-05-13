(() => {
  const body = document.body;

  if (!body) {
    return;
  }

  const searchUrl = body.dataset.searchUrl || '/dashboard/busca/';

  const focusSearchInput = () => {
    const input = document.querySelector('[data-search-input]');

    if (body.dataset.searchPage === 'true' && input) {
      input.focus();
      input.select();
      return true;
    }

    return false;
  };

  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();

      if (!focusSearchInput() && searchUrl) {
        window.location.href = searchUrl;
      }
    }
  });
})();
