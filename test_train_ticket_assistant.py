import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import train_ticket_assistant as assistant
import irctc_browser


class ValidationTests(unittest.TestCase):
    def journey(self, **changes):
        values = dict(origin="NDLS", destination="BCT", date="03-01-2099",
                      quota="TATKAL", train_origin_date="02-01-2099")
        values.update(changes)
        return assistant.Journey(**values)

    def test_tatkal_uses_origin_date_and_class(self):
        opening = assistant.tatkal_opening(self.journey(travel_class="3E"))
        self.assertEqual(opening.strftime("%d-%m-%Y %H:%M"), "01-01-2099 10:00")
        self.assertEqual(opening.utcoffset(), timedelta(hours=5, minutes=30))
        self.assertEqual(assistant.tatkal_opening(self.journey()).hour, 11)

    def test_origin_date_must_be_confirmed(self):
        with self.assertRaises(ValueError):
            assistant.tatkal_opening(self.journey(train_origin_date=""))

    def test_invalid_journeys(self):
        for changes in ({"origin": ""}, {"destination": "ndls"},
                        {"travel_class": "1A"}, {"train_no": "123"},
                        {"date": "01-01-2000"}, {"train_origin_date": "04-01-2099"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                assistant.validate_journey(self.journey(**changes))

    def test_invalid_passengers(self):
        for age in (0, -1, 126, True, "20"):
            with self.subTest(age=age), self.assertRaises(ValueError):
                assistant.validate_passenger(assistant.Passenger("Test", age, "Male"))

    def test_station_matching_is_exact(self):
        self.assertRegex("NEW DELHI - NDLS (NEW DELHI)DELHI", irctc_browser.station_pattern("NDLS"))
        self.assertNotRegex("OTHER - NDLSX", irctc_browser.station_pattern("NDLS"))
        self.assertRegex("NEW DELHI - NDLS", irctc_browser.station_pattern("New Delhi"))
        self.assertNotRegex("DELHI CANTT - DEC", irctc_browser.station_pattern("Delhi"))

    def test_brief_limits(self):
        passenger = assistant.Passenger("Example Passenger", 25, "Female")
        self.assertIn("Example Passenger", assistant.booking_brief(self.journey(), [passenger]))
        for passengers in ([], [passenger] * 5):
            with self.assertRaises(ValueError):
                assistant.booking_brief(self.journey(), passengers)

    def test_calendar_utc_and_alarm(self):
        with TemporaryDirectory() as directory:
            content = assistant.export_reminder(self.journey(), Path(directory)).read_bytes()
        self.assertIn(b"DTSTART:20990101T053000Z\r\n", content)
        self.assertIn(b"TRIGGER:-PT5M\r\n", content)
        self.assertTrue(content.endswith(b"END:VCALENDAR\r\n"))

    def test_atomic_store_and_legacy_schema(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            with patch.object(assistant, "DATA_FILE", path):
                store = assistant.Store(journeys=[self.journey()])
                assistant.save_store(store)
                self.assertEqual(assistant.load_store(), store)
                original = path.read_bytes()
                with patch.object(assistant.os, "replace", side_effect=OSError("locked")):
                    with self.assertRaises(OSError):
                        assistant.save_store(assistant.Store())
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(Path(directory).glob("*.tmp")), [])
                path.write_text('{"journeys":[{"origin":"A","destination":"B","date":"01-01-2099"}]}')
                self.assertEqual(assistant.load_store().journeys[0].train_origin_date, "")
                path.write_text('{"journeys":[{"origin":"A","destination":"B","date":"01-01-2099","train_no":"123"}]}')
                legacy = assistant.load_store().journeys[0]
                self.assertEqual(legacy.train_no, "123")
                with self.assertRaises(ValueError):
                    assistant.validate_journey(legacy)


class ConfigTests(unittest.TestCase):
    def config(self, **changes):
        raw = {
            "journey": {"origin": "NDLS", "destination": "BCT", "date": "03-01-2099",
                        "quota": "GENERAL", "travel_class": "SL"},
            "passengers": [{"name": "Example", "age": 25, "gender": "Female"}],
        }
        raw.update(changes)
        return raw

    def load(self, raw):
        with patch.object(Path, "open", return_value=MagicMock()), patch.object(assistant.tomllib, "load", return_value=raw):
            return assistant.load_booking_config(Path("test.toml"))

    def test_general_and_tatkal_config(self):
        raw = self.config()
        self.assertEqual(self.load(raw)[0].quota, "GENERAL")
        raw["journey"].update(quota="TATKAL", train_origin_date="02-01-2099")
        self.assertEqual(self.load(raw)[0].quota, "TATKAL")
        raw["journey"]["train_origin_date"] = ""
        with self.assertRaises(ValueError):
            self.load(raw)

    def test_invalid_config_shapes_and_types(self):
        for raw in ({}, self.config(password="not-supported"), self.config(journey=[]),
                    self.config(passengers=[]), self.config(passengers=["name"]),
                    self.config(passengers=[{"name": "Test", "age": "25", "gender": "Male"}]),
                    self.config(passengers=[{"name": "Test", "age": 25, "gender": []}])):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.load(raw)

    def test_template_parses_but_needs_details(self):
        with assistant.CONFIG_FILE.open("rb") as source:
            raw = assistant.tomllib.load(source)
        self.assertEqual(raw["journey"]["quota"], "GENERAL")
        with self.assertRaises(ValueError):
            assistant.load_booking_config(assistant.CONFIG_FILE)

    def test_check_only_never_opens_browser_or_saves(self):
        journey, passengers = self.load(self.config())
        with patch.object(assistant, "load_booking_config", return_value=(journey, passengers)), patch.object(irctc_browser, "assist_booking") as browser, patch.object(assistant, "save_store") as save:
            assistant.run_booking_config(Path("test.toml"), check_only=True)
        browser.assert_not_called()
        save.assert_not_called()

    def test_config_passes_details_to_browser_without_menu(self):
        journey, passengers = self.load(self.config())
        with patch.object(assistant, "load_booking_config", return_value=(journey, passengers)), patch.object(irctc_browser, "assist_booking") as browser, patch("builtins.input") as prompt:
            assistant.run_booking_config(Path("test.toml"))
        browser.assert_called_once_with(journey, passengers)
        prompt.assert_not_called()


class BrowserTests(unittest.TestCase):
    def test_search_fills_without_submitting(self):
        page = MagicMock()
        page.locator.return_value.input_value.return_value = "03/01/2099"
        journey = assistant.Journey("NDLS", "BCT", "03-01-2099")
        with patch.object(irctc_browser, "fill_station") as station, patch.object(irctc_browser, "choose_dropdown") as dropdown:
            irctc_browser.fill_search(page, journey)
        self.assertEqual(station.call_count, 2)
        self.assertEqual(dropdown.call_count, 2)
        page.locator.return_value.fill.assert_called_once_with("03/01/2099")
        page.get_by_role.assert_not_called()
        page.locator.return_value.click.assert_not_called()

    def test_passenger_count_mismatch_does_not_fill(self):
        page = MagicMock()
        page.locator.return_value.count.return_value = 2
        with self.assertRaises(RuntimeError):
            irctc_browser.fill_passengers(page, [assistant.Passenger("Test", 25, "Male")])
        page.locator.return_value.nth.assert_not_called()

    def test_passenger_fields_filled_without_submit(self):
        page = MagicMock()
        names = page.locator.return_value
        names.count.return_value = 1
        name = names.nth.return_value
        row = name.locator.return_value
        row.count.return_value = 1
        row.locator.return_value.count.return_value = 1
        passenger = assistant.Passenger("Example Passenger", 25, "Female", "Lower")
        with patch.object(irctc_browser, "select_native") as select:
            irctc_browser.fill_passengers(page, [passenger])
        name.fill.assert_called_once_with("Example Passenger")
        row.locator.return_value.fill.assert_called_once_with("25")
        self.assertEqual(select.call_count, 2)
        page.get_by_role.assert_not_called()
        name.click.assert_not_called()

    def test_ambiguous_station_does_not_click(self):
        page = MagicMock()
        options = page.locator.return_value.filter.return_value
        options.count.return_value = 2
        with self.assertRaises(RuntimeError):
            irctc_browser.fill_station(page, "From", "NDLS")
        options.click.assert_not_called()

    def test_official_origin_required(self):
        page = MagicMock()
        for url in ("https://example.com", "http://www.irctc.co.in", "https://www.irctc.co.in.example.com"):
            page.url = url
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                irctc_browser.require_irctc(page)
        page.url = assistant.IRCTC_URL
        irctc_browser.require_irctc(page)


class PnrTests(unittest.TestCase):
    def test_missing_configuration_does_not_request(self):
        with patch.dict(assistant.os.environ, {}, clear=True), patch.object(assistant.urllib.request, "build_opener") as opener:
            assistant.check_pnr_status()
        opener.assert_not_called()

    def test_insecure_endpoint_rejected(self):
        environment = {"PNR_API_KEY": "test-only", "PNR_API_URL": "http://example.com/{pnr}"}
        with patch.dict(assistant.os.environ, environment, clear=True), patch.object(assistant, "ask", return_value="1234567890"):
            with self.assertRaises(ValueError):
                assistant.check_pnr_status()

    def test_successful_lookup(self):
        environment = {"PNR_API_KEY": "test-only", "PNR_API_URL": "https://example.com/{pnr}"}
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"status":"confirmed"}'
        with patch.dict(assistant.os.environ, environment, clear=True), patch.object(assistant, "ask", return_value="1234567890"), patch.object(assistant.urllib.request, "build_opener", return_value=opener), patch("builtins.print") as output:
            assistant.check_pnr_status()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.com/1234567890")
        self.assertIn("confirmed", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()