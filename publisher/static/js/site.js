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

  // Temporal gravity filter
  window.__filterTemporal = function(btn, hours) {
    document.querySelectorAll('.temporal-btn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');

    if (hours === 0) {
      cards.forEach(function(card) { card.style.display = ''; });
      return;
    }

    var cutoff = Date.now() - (hours * 3600 * 1000);
    var visible = 0;
    cards.forEach(function(card) {
      var tsEl = card.querySelector('.card__ts');
      if (!tsEl) { card.style.display = ''; visible++; return; }
      var ts = tsEl.getAttribute('data-ts') || tsEl.textContent.trim();
      var d = new Date(ts);
      if (isNaN(d.getTime()) || d.getTime() >= cutoff) {
        card.style.display = '';
        visible++;
      } else {
        card.style.display = 'none';
      }
    });

    var emptyMsg = document.getElementById('js-temporal-empty');
    if (visible === 0) {
      if (!emptyMsg) {
        var el = document.createElement('div');
        el.id = 'js-temporal-empty';
        el.className = 'temporal-empty';
        el.textContent = '0 Signals Ingested in Current Window — Adjust Temporal Gate to View Deep Context.';
        var feed = document.getElementById('js-feed');
        if (feed) feed.parentNode.insertBefore(el, feed);
      }
    } else if (emptyMsg) {
      emptyMsg.remove();
    }
  };
})();
