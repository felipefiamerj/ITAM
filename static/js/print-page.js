(() => {
  document.querySelectorAll('[data-print-page]').forEach((button) => {
    button.addEventListener('click', () => {
      window.print();
    });
  });
})();
