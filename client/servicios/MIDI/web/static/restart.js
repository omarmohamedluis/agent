(function retry() {
  fetch('/')
    .then(function () {
      window.location.href = '/';
    })
    .catch(function () {
      setTimeout(retry, 800);
    });
})();
