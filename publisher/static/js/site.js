(function () {
  'use strict';

  // Stream filter pills
  var pills = document.querySelectorAll('.js-pill');
  var cards = document.querySelectorAll('.js-card');

  pills.forEach(function (pill) {
    pill.addEventListener('click', function () {
      pills.forEach(function (p) { p.classList.remove('pill--active'); });
      pill.classList.add('pill--active');

      var stream = pill.dataset.stream;
      cards.forEach(function (card) {
        if (stream === 'all' || card.dataset.stream === stream) {
          card.style.display = '';
        } else {
          card.style.display = 'none';
        }
      });
    });
  });
})();
