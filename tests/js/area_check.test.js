// Plain-function tests for publisher/static/js/area-check.js (Area check).
// Run: node --test tests/js/area_check.test.js   (also run by tests/test_area_check.py)
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const A = require(path.join(__dirname, '..', '..', 'publisher', 'static', 'js', 'area-check.js'));

// Points from the plan's own edge cases (pin files and OSM).
const BONTEHEUWEL = [-33.94917, 18.55306];
const JAKKALSVLEI = [-33.94476, 18.54334];       // 2a848e46, street level
const LANGA = [-33.9469, 18.5318];                // 7acad00a, street level

test('distance matches the plan: Jakkalsvlei pin about 1,021 m from Bonteheuwel', () => {
  const d = A.distanceM(BONTEHEUWEL[0], BONTEHEUWEL[1], JAKKALSVLEI[0], JAKKALSVLEI[1]);
  assert.ok(Math.abs(d - 1021) < 15, `got ${d}`);
});

test('distance is symmetric and zero at the same point', () => {
  assert.equal(A.distanceM(-33.9, 18.5, -33.9, 18.5), 0);
  const a = A.distanceM(-33.9, 18.5, -34.0, 18.6), b = A.distanceM(-34.0, 18.6, -33.9, 18.5);
  assert.ok(Math.abs(a - b) < 1e-6);
});

test('inside only when the whole fuzzy edge fits; may-be-inside when it overlaps', () => {
  assert.equal(A.classify(1021, 500, 2000), 'in');       // 1,521 <= 2,000
  assert.equal(A.classify(1021, 500, 1000), 'maybe');    // just outside 1 km, but street-level
  assert.equal(A.classify(1977, 300, 2000), 'maybe');
  assert.equal(A.classify(2600, 300, 2000), 'out');
  assert.equal(A.classify(400, 0, 500), 'in');           // no precision entry: a point
  assert.equal(A.classify(500, 0, 500), 'in');           // the edge counts
  assert.equal(A.classify(501, undefined, 500), 'out');
});

test('Langa from Bonteheuwel: under 2 km but only may-be-inside at street level', () => {
  const d = A.distanceM(BONTEHEUWEL[0], BONTEHEUWEL[1], LANGA[0], LANGA[1]);
  assert.ok(d < 2000 && d > 1800, `got ${d}`);
  assert.equal(A.classify(d, 300, 2000), 'maybe');
});

test('South African day: 23:30 UTC is the next day in SA', () => {
  assert.equal(A.saDay(Date.parse('2026-10-14T21:59:59Z')), '2026-10-14');
  assert.equal(A.saDay(Date.parse('2026-10-14T22:00:00Z')), '2026-10-15');
});

test('pin days: a whole day, a night across midnight, a stated time', () => {
  // day precision: 15 Oct 00:00 SAST .. 16 Oct 00:00 SAST (end exclusive)
  assert.deepEqual(A.pinDays({ incident_at: '2026-10-14T22:00:00Z', incident_end: '2026-10-15T22:00:00Z' }),
                   { start: '2026-10-15', end: '2026-10-15' });
  // night: 6 Oct 20:00 .. 7 Oct 06:00 SAST
  assert.deepEqual(A.pinDays({ incident_at: '2026-10-06T18:00:00Z', incident_end: '2026-10-07T04:00:00Z' }),
                   { start: '2026-10-06', end: '2026-10-07' });
  // stated time: start == end
  assert.deepEqual(A.pinDays({ incident_at: '2026-10-09T15:30:00Z', incident_end: '2026-10-09T15:30:00Z' }),
                   { start: '2026-10-09', end: '2026-10-09' });
  assert.equal(A.pinDays({ incident_at: null }), null);
  assert.equal(A.pinDays({ incident_at: 'not a date' }), null);
});

test('date ranges and overlap', () => {
  assert.equal(A.rangeFor('all', '2026-10-19'), null);
  assert.deepEqual(A.rangeFor('7', '2026-10-19'), { from: '2026-10-13', to: '2026-10-19' });
  assert.deepEqual(A.rangeFor('30', '2026-10-19'), { from: '2026-09-20', to: '2026-10-19' });
  assert.deepEqual(A.rangeFor('custom', '2026-10-19', '2026-10-10', '2026-10-01'), { from: '2026-10-01', to: '2026-10-10' });
  assert.equal(A.rangeFor('custom', '2026-10-19', '2026-13-01', '2026-10-01'), null);
  const night = { incident_at: '2026-10-06T18:00:00Z', incident_end: '2026-10-07T04:00:00Z' };
  assert.equal(A.dateMatch(night, { from: '2026-10-07', to: '2026-10-19' }), true);   // touches the 7th
  assert.equal(A.dateMatch(night, { from: '2026-10-08', to: '2026-10-19' }), false);
  assert.equal(A.dateMatch(night, null), true);
  assert.equal(A.dateMatch({ incident_at: null }, { from: '2026-10-08', to: '2026-10-19' }), 'unknown');
});

test('valid day rejects impossible dates', () => {
  assert.ok(A.validDay('2026-10-19'));
  assert.ok(!A.validDay('2026-02-30'));
  assert.ok(!A.validDay('19/10/2026'));
  assert.ok(!A.validDay(null));
});

const PLACES = {
  kinds: ['city', 'town', 'town or village', 'suburb', 'locality', 'area', 'village'],
  provinces: ['Eastern Cape', 'Western Cape'],
  municipalities: ['City of Cape Town', 'Drakenstein', 'Nelson Mandela Bay', 'Breede Valley'],
  rows: [
    ['Gqeberha', -33.96, 25.61, 0, 0, 2, ['Port Elizabeth']],
    ['Wesbank', -33.97, 18.67, 3, 1, 0, 0],
    ['Wesbank', -33.75, 18.97, 3, 1, 1, 0],
    ['Bonteheuwel', -33.94917, 18.55306, 3, 1, 0, 0],
    ['Asanda Village', -34.10647, 18.84959, 3, 1, 0, ['Zola Village']],
    ['Zwelethemba', -33.65083, 19.49361, 3, 1, 3, ['Zweletemba']],
    ['Hanover Park', -33.99, 18.55, 3, 1, 0, 0]
  ]
};

test('place search: exact, alias, word start, accents and hyphens', () => {
  assert.equal(A.searchPlaces(PLACES, 'Bonteheuwel')[0].name, 'Bonteheuwel');
  const pe = A.searchPlaces(PLACES, 'port elizabeth');
  assert.equal(pe[0].name, 'Gqeberha'); assert.equal(pe[0].alias, 'Port Elizabeth');
  assert.equal(A.searchPlaces(PLACES, 'zola')[0].name, 'Asanda Village');
  assert.equal(A.searchPlaces(PLACES, 'village').length, 1);
  assert.equal(A.searchPlaces(PLACES, 'HANOVER-PARK')[0].name, 'Hanover Park');
  assert.equal(A.searchPlaces(PLACES, 'z').length, 0);              // too short
  assert.equal(A.searchPlaces(PLACES, 'nowhereville').length, 0);
});

test('place search never auto-picks: both Wesbanks come back, and a comma only re-ranks', () => {
  const w = A.searchPlaces(PLACES, 'Wesbank');
  assert.equal(w.length, 2);
  assert.deepEqual(w.map(x => x.municipality).sort(), ['City of Cape Town', 'Drakenstein']);
  const w2 = A.searchPlaces(PLACES, 'Wesbank, Drakenstein');
  assert.equal(w2.length, 2);
  assert.equal(w2[0].municipality, 'Drakenstein');
  assert.ok(A.searchPlaces(PLACES, 'a', 5).length <= 5);
});

test('saved place: kept only when every part checks out', () => {
  const ok = { v: 1, lat: -33.99, lng: 18.55, label: 'Hanover Park', kind: 'suburb', radius: 2000 };
  assert.equal(A.parseSaved(JSON.stringify(ok)).label, 'Hanover Park');
  assert.equal(A.parseSaved(JSON.stringify(ok)).dates, 'all');
  assert.equal(A.parseSaved(JSON.stringify({ ...ok, radius: 3000 })), null);      // unknown radius
  assert.equal(A.parseSaved(JSON.stringify({ ...ok, lat: 51.5, lng: -0.1 })), null); // outside SA
  assert.equal(A.parseSaved(JSON.stringify({ ...ok, v: 2 })), null);
  assert.equal(A.parseSaved(JSON.stringify({ ...ok, label: '' })), null);
  assert.equal(A.parseSaved(JSON.stringify({ ...ok, label: 'x'.repeat(81) })), null);
  assert.equal(A.parseSaved('{not json'), null);
  assert.equal(A.parseSaved(JSON.stringify({ ...ok, dates: 'custom', from: '2026-10-01' })).dates, 'all');
  assert.equal(A.parseSaved(JSON.stringify({ ...ok, on: false })).on, false);
});

test('saved points are rounded to about 11 m', () => {
  assert.equal(A.roundPoint(-33.949173219), -33.9492);
  assert.equal(A.roundPoint(18.553061), 18.5531);
});

test('summary wording', () => {
  assert.equal(A.summaryText({ inside: 2, maybe: 0, unknown: 0 }, 2000, null), '2 reported killings within 2 km.');
  assert.equal(A.summaryText({ inside: 1, maybe: 1, unknown: 0 }, 500, null),
               '1 reported killing within 500 m. 1 more may be inside: its location is approximate.');
  assert.match(A.summaryText({ inside: 0, maybe: 0, unknown: 0 }, 1000, null), /does not mean the area is safe/);
  assert.match(A.summaryText({ inside: 1, maybe: 0, unknown: 0 }, 1000, { from: '2026-10-13', to: '2026-10-19' }),
               /between 13 Oct and 19 Oct/);
  assert.equal(A.fmtSpan({ start: '2026-10-06', end: '2026-10-07' }), '6\u20137 Oct');
  assert.equal(A.fmtSpan({ start: '2026-10-31', end: '2026-11-01' }), '31 Oct\u20131 Nov');
});
