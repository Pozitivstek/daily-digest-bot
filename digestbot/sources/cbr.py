"""Курсы валют ЦБ РФ (официальный XML, кодировка windows-1251)."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import ParseError as XmlParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from ..errors import ParseError

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"


@dataclass(frozen=True)
class Rate:
    code: str
    name: str
    nominal: int
    value: Decimal   # рублей за nominal единиц


@dataclass(frozen=True)
class RatesSnapshot:
    on_date: date
    rates: dict[str, Rate]


def _decimal(text: str | None) -> Decimal:
    return Decimal((text or "").strip().replace(",", ".").replace(" ", ""))


def parse_rates(raw: bytes) -> RatesSnapshot:
    try:
        root = ElementTree.fromstring(raw)
    except (XmlParseError, DefusedXmlException, ValueError) as exc:
        raise ParseError(f"курсы ЦБ: не XML ({type(exc).__name__})") from exc

    try:
        on_date = datetime.strptime(root.get("Date") or "", "%d.%m.%Y").date()
    except ValueError as exc:
        raise ParseError("курсы ЦБ: нет даты в ответе") from exc

    rates: dict[str, Rate] = {}
    for node in root.iter("Valute"):
        try:
            code = (node.findtext("CharCode") or "").strip().upper()
            rate = Rate(
                code=code,
                name=(node.findtext("Name") or code).strip(),
                nominal=int(node.findtext("Nominal") or "1"),
                value=_decimal(node.findtext("Value")),
            )
        except (ValueError, InvalidOperation):
            continue   # одна битая валюта не должна ронять весь ответ
        if code and rate.nominal > 0 and rate.value > 0:
            rates[code] = rate
    if not rates:
        raise ParseError("курсы ЦБ: в ответе нет валют")
    return RatesSnapshot(on_date, rates)


async def fetch_rates(fetcher, for_date: date | None = None) -> RatesSnapshot:
    params = {"date_req": for_date.strftime("%d/%m/%Y")} if for_date else None
    return parse_rates(await fetcher.get(CBR_URL, params))
