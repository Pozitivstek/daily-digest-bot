"""Парсеры внешних источников: ЦБ РФ, геокодер и прогноз Open-Meteo, RSS/Atom."""
import json
import unittest
from datetime import date
from decimal import Decimal

from digestbot.errors import ParseError
from digestbot.sources.cbr import parse_rates
from digestbot.sources.geo import parse_places
from digestbot.sources.rss import parse_feed
from digestbot.sources.weather import describe, parse_forecast

CBR_XML = (
    '<?xml version="1.0" encoding="windows-1251"?>'
    '<ValCurs Date="10.10.2026" name="Foreign Currency Market">'
    '<Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal>'
    "<Name>Доллар США</Name><Value>95,1234</Value><VunitRate>95,1234</VunitRate></Valute>"
    '<Valute ID="R01335"><NumCode>398</NumCode><CharCode>KZT</CharCode><Nominal>100</Nominal>'
    "<Name>Казахстанских тенге</Name><Value>17,9500</Value><VunitRate>0,1795</VunitRate></Valute>"
    "</ValCurs>"
).encode("cp1251")


class CbrTest(unittest.TestCase):
    def test_parses_date_and_rates(self):
        snap = parse_rates(CBR_XML)
        self.assertEqual(snap.on_date, date(2026, 10, 10))
        self.assertEqual(snap.rates["USD"].value, Decimal("95.1234"))
        self.assertEqual(snap.rates["USD"].name, "Доллар США")

    def test_nominal_is_kept(self):
        self.assertEqual(parse_rates(CBR_XML).rates["KZT"].nominal, 100)

    def test_garbage_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse_rates(b"<html>not xml at all")

    def test_xml_entity_bomb_is_rejected(self):
        bomb = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">'
                b'<!ENTITY b "&a;&a;&a;&a;">]><ValCurs Date="10.10.2026">&b;</ValCurs>')
        with self.assertRaises(ParseError):
            parse_rates(bomb)

    def test_missing_date_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse_rates(b'<?xml version="1.0"?><ValCurs></ValCurs>')


GEO_JSON = json.dumps({"results": [
    {"name": "Казань", "latitude": 55.78874, "longitude": 49.12214, "timezone": "Europe/Moscow",
     "country": "Россия", "admin1": "Татарстан"},
    {"name": "Казань", "latitude": 59.16, "longitude": 49.93, "timezone": "Europe/Kirov",
     "country": "Россия", "admin1": "Кировская Область"},
    {"name": "Без пояса", "latitude": 1.0, "longitude": 2.0},
]}).encode()


class GeoTest(unittest.TestCase):
    def test_parses_places_and_skips_incomplete(self):
        places = parse_places(GEO_JSON)
        self.assertEqual(len(places), 2)
        self.assertEqual(places[0].tz, "Europe/Moscow")
        self.assertAlmostEqual(places[0].lat, 55.78874)

    def test_label_has_region_and_country(self):
        self.assertEqual(parse_places(GEO_JSON)[0].label, "Казань, Татарстан, Россия")

    def test_no_results_key_means_empty_list(self):
        self.assertEqual(parse_places(b'{"generationtime_ms": 1.0}'), [])

    def test_non_json_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse_places(b"<html>403</html>")


FORECAST_JSON = json.dumps({
    "current": {"temperature_2m": 15.3, "weather_code": 3},
    "daily": {"weather_code": [80], "temperature_2m_max": [15.3], "temperature_2m_min": [11.1],
              "precipitation_probability_max": [78], "wind_speed_10m_max": [9.2]},
}).encode()


class WeatherTest(unittest.TestCase):
    def test_parses_forecast(self):
        w = parse_forecast(FORECAST_JSON)
        self.assertEqual((w.temp_now, w.code), (15.3, 3))
        self.assertEqual((w.temp_max, w.temp_min, w.precip_prob), (15.3, 11.1, 78))
        self.assertEqual(w.day_code, 80)
        self.assertEqual(w.wind_max, 9.2)

    def test_null_precipitation_is_allowed(self):
        data = json.loads(FORECAST_JSON)
        data["daily"]["precipitation_probability_max"] = [None]
        self.assertIsNone(parse_forecast(json.dumps(data).encode()).precip_prob)

    def test_missing_fields_raise_parse_error(self):
        with self.assertRaises(ParseError):
            parse_forecast(b'{"current": {}}')

    def test_describe_known_and_unknown_codes(self):
        self.assertIn("ливн", describe(80)[1])
        self.assertTrue(describe(12345)[1])   # неизвестный код — запасной текст, не исключение


RSS2 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Новости</title>
<item><title><![CDATA[Первая <b>новость</b> &amp; всё]]></title>
<link>https://habr.com/ru/news/1/?utm_source=habrahabr&amp;utm_medium=rss&amp;id=7</link></item>
<item><title>Вторая</title><link>https://example.com/2</link></item>
<item><title>Опасная</title><link>javascript:alert(1)</link></item>
<item><title></title><link>https://example.com/empty</link></item>
<item><title>Третья</title><link>https://example.com/3</link></item>
</channel></rss>""".encode()

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Real Python</title>
<link href="https://realpython.com/atom.xml" rel="self"/>
<entry><title>Functional Programming</title>
<link href="https://realpython.com/python-functional-programming/"/></entry>
<entry><title>Second</title><link href="https://realpython.com/second/"/></entry>
</feed>""".encode()


class RssTest(unittest.TestCase):
    def test_rss2_items_are_cleaned(self):
        items = parse_feed(RSS2, limit=10)
        self.assertEqual(items[0].title, "Первая новость & всё")
        self.assertEqual(items[0].link, "https://habr.com/ru/news/1/?id=7")   # utm_* убраны, id остался

    def test_unsafe_and_empty_items_are_skipped(self):
        titles = [i.title for i in parse_feed(RSS2, limit=10)]
        self.assertNotIn("Опасная", titles)
        self.assertEqual(titles, ["Первая новость & всё", "Вторая", "Третья"])

    def test_limit(self):
        self.assertEqual(len(parse_feed(RSS2, limit=2)), 2)

    def test_atom_entries(self):
        items = parse_feed(ATOM, limit=5)
        self.assertEqual([i.title for i in items], ["Functional Programming", "Second"])
        self.assertEqual(items[0].link, "https://realpython.com/python-functional-programming/")

    def test_broken_xml_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse_feed(b"<rss><channel><item>", limit=3)

    def test_unknown_format_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse_feed(b"<html><body>hi</body></html>", limit=3)


if __name__ == "__main__":
    unittest.main()
