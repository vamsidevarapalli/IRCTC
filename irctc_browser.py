from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page
    from train_ticket_assistant import Journey, Passenger


def station_pattern(value: str) -> re.Pattern[str]:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z]{1,5}", value):
        return re.compile(r"-\s*" + re.escape(value) + r"(?=\s|\(|$)", re.IGNORECASE)
    return re.compile(r"^\s*" + re.escape(value) + r"\s*(?:-|$)", re.IGNORECASE)


def fill_station(page: Page, label: str, value: str) -> None:
    field = page.get_by_role("searchbox", name=label, exact=True)
    field.fill(value)
    suggestions_id = field.get_attribute("aria-controls")
    if not suggestions_id:
        raise RuntimeError("Station suggestions are unavailable. Select the station manually.")
    options = page.locator(f'[id="{suggestions_id}"] [role="option"]').filter(has_text=station_pattern(value))
    options.first.wait_for(state="visible")
    if options.count() != 1:
        raise RuntimeError(f"Station {value!r} is ambiguous. Use its exact station code.")
    options.click()


def choose_dropdown(page: Page, selector: str, pattern: re.Pattern[str]) -> None:
    page.locator(selector).click()
    options = page.locator('[role="option"]:visible').filter(has_text=pattern)
    options.first.wait_for(state="visible")
    if options.count() != 1:
        raise RuntimeError("Could not identify one class/quota option. Select it manually.")
    options.click()


def fill_search(page: Page, journey: Journey) -> None:
    fill_station(page, "Enter From station. Input is Mandatory.", journey.origin)
    fill_station(page, "Enter To station. Input is Mandatory.", journey.destination)
    date_field = page.locator('#jDate input')
    value = datetime.strptime(journey.date, "%d-%m-%Y").strftime("%d/%m/%Y")
    date_field.fill(value)
    date_field.press("Tab")
    if date_field.input_value() != value:
        raise RuntimeError("IRCTC did not retain the requested date. Enter it manually.")
    choose_dropdown(page, "#journeyClass", re.compile(r"\(" + re.escape(journey.travel_class) + r"\)"))
    choose_dropdown(page, "#journeyQuota", re.compile(r"^\s*" + re.escape(journey.quota) + r"\s*$"))


def select_native(field: Locator, labels: list[str], values: list[str]) -> None:
    if field.count() != 1:
        raise RuntimeError("Expected one passenger dropdown. Complete this field manually.")
    options = field.locator("option")
    for index in range(options.count()):
        option = options.nth(index)
        label = option.inner_text().strip().casefold()
        value = option.get_attribute("value")
        if label in {entry.casefold() for entry in labels} or value in values:
            field.select_option(index=index)
            return
    raise RuntimeError(f"Passenger option {labels[0]!r} is unavailable. Select it manually.")


def fill_passengers(page: Page, passengers: list[Passenger]) -> None:
    names = page.locator('input[formcontrolname="passengerName"]:visible, input[placeholder="Passenger Name"]:visible')
    if names.count() != len(passengers):
        raise RuntimeError(f"Add exactly {len(passengers)} passenger rows on IRCTC before filling; no rows were changed.")
    genders = {"Male": "M", "Female": "F", "Transgender": "T"}
    berths = {"Lower": "LB", "Middle": "MB", "Upper": "UB", "Side Lower": "SL", "Side Upper": "SU"}
    for index, passenger in enumerate(passengers):
        name = names.nth(index)
        row = name.locator("xpath=ancestor::*[.//input[@formcontrolname='passengerAge' or @placeholder='Age']][1]")
        age = row.locator('input[formcontrolname="passengerAge"], input[placeholder="Age"]')
        gender = row.locator('select[formcontrolname="passengerGender"]')
        berth = row.locator('select[formcontrolname="passengerBerthChoice"]')
        if row.count() != 1 or age.count() != 1 or gender.count() != 1 or berth.count() != 1:
            raise RuntimeError("Passenger form layout is not supported. Review any filled rows and finish manually.")
        name.fill(passenger.name)
        name.press("Tab")
        age.fill(str(passenger.age))
        select_native(gender, [passenger.gender], [genders[passenger.gender]])
        if passenger.berth == "No preference":
            select_native(berth, ["No preference", "No Preference", "Select Berth Preference"], ["", "0", "-1"])
        else:
            select_native(berth, [passenger.berth, passenger.berth + " Berth"], [berths[passenger.berth]])


def require_irctc(page: Page) -> None:
    location = urlsplit(page.url)
    if location.scheme != "https" or location.hostname != "www.irctc.co.in":
        raise RuntimeError("Stopped: the current page is not the official IRCTC website.")


def assist_booking(journey: Journey, passengers: list[Passenger]) -> None:
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install browser support: python -m pip install -r requirements.txt") from exc
    from train_ticket_assistant import IRCTC_URL, booking_brief

    booking_brief(journey, passengers)
    print("A temporary Chrome session will open. Credentials and browser sessions are not saved.")
    print("Enter secrets only in the browser. Type q at a terminal checkpoint to stop.")
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=False)
        except Error as exc:
            raise RuntimeError("Could not automate Google Chrome. It may be missing or blocked by administrator policy. "
                               "Use menu 7 for manual booking; ask IT to approve browser automation if needed.") from exc
        try:
            page = browser.new_page()
            page.set_default_timeout(12000)
            page.goto(IRCTC_URL, wait_until="domcontentloaded", timeout=60000)
            response = input("In Chrome, dismiss notices, log in manually, and open train search. Enter to fill, q to stop: ")
            if response.strip().lower() == "q":
                return
            require_irctc(page)
            try:
                fill_search(page, journey)
                print("Search fields filled. Review stations/date/class/quota, then click Search yourself.")
            except (Error, RuntimeError) as exc:
                print(f"Search filling stopped ({type(exc).__name__}). Review and complete the search manually.")
            print(f"Select train {journey.train_no or 'of your choice'}, confirm fare/availability, and open Passenger Details.")
            print(f"Add exactly {len(passengers)} passenger rows. This assistant does not click Book Now or Continue.")
            while True:
                response = input("Confirm the journey is correct and passenger rows are ready. Enter to fill, q to stop: ")
                if response.strip().lower() == "q":
                    return
                require_irctc(page)
                try:
                    fill_passengers(page, passengers)
                    print("Passenger fields filled. Review every value, then complete contact details, CAPTCHA/OTP and payment yourself.")
                    break
                except (Error, RuntimeError) as exc:
                    print(f"Passenger filling stopped ({type(exc).__name__}). Check the rows; existing fields may be partly filled.")
                    if input("Enter r to retry after fixing the form, or Enter to finish manually: ").strip().lower() != "r":
                        break
            input("The browser stays open while you finish. Press Enter only when ready to CLOSE this session: ")
        except Error as exc:
            raise RuntimeError("Browser closed or navigation failed. No booking was submitted by the assistant.") from exc
        finally:
            browser.close()