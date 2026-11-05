"""FAA SDR 'AircraftModel' kodlarını aile / nesil / gövde tipine eşleyen kurallar.

Boeing kodları: <tip><seri><müşteri kodu>  ör. 7378H4 = 737-8H4 (737NG -800, Southwest)
  - Müşteri kodu olmayan 7377/7378/7379/73710 ve 7378200 => 737 MAX (737-7/-8/-9/-10)
Airbus kodları: A320214 = A320-214 (ceo); sonu N/NX olanlar => neo; A3309xx => A330neo
A220 (eski Bombardier CSeries) SDR'de AIRBUS / BD500-1A10 / BD500-1A11 olarak geçer.
"""
import re

GENERIC = {"737", "737*", "737800*", "7377*", "757", "757*", "767", "767*", "777", "777*",
           "747", "747*", "787", "787*", "A300*", "A320*", "A330*", "100BOEING"}


def parse_model(make: str, model: str):
    """(aile, nesil, gövde) döndürür; tanınamazsa None."""
    if not isinstance(model, str):
        return None
    m = model.strip().upper().replace("-", "").replace(" ", "")
    if m in GENERIC:
        return None
    if make == "BOEING":
        if m.startswith("717"):
            return ("B717", "B717", "Dar")
        if m.startswith("727"):
            return ("B727", "B727", "Dar")
        mm = re.match(r"^737(\d)(.*)$", m)
        if mm:
            d, rest = mm.groups()
            if m in ("7377", "7378", "7379", "73710", "7378200") or m.startswith("7378200") \
                    or "MAX" in rest:
                return ("B737", "B737 MAX", "Dar")
            if d in "2345":
                return ("B737", "B737 Classic", "Dar")
            if d in "6789":
                return ("B737", "B737 NG", "Dar")
            return None
        if m.startswith("757"):
            return ("B757", "B757", "Dar")
        if m.startswith("767"):
            return ("B767", "B767", "Genis")
        if m.startswith("777"):
            return ("B777", "B777", "Genis")
        if m.startswith("787"):
            return ("B787", "B787", "Genis")
        if m.startswith("747"):
            return ("B747", "B747", "Genis")
        return None
    if make == "AIRBUS":
        if m.startswith("BD500"):
            return ("A220", "A220", "Dar")
        mm = re.match(r"^A3(19|20|21)(\d{3})(.*)$", m)
        if mm:
            fam = "A3" + mm.group(1)
            neo = mm.group(3) in ("N", "NX", "NEO", "LR", "XLR") or mm.group(3).startswith("N")
            return (fam, "A320neo ailesi" if neo else "A320ceo ailesi", "Dar")
        if m.startswith("A300"):
            return ("A300", "A300", "Genis")
        if m.startswith("A310"):
            return ("A310", "A310", "Genis")
        if m.startswith("A3309"):
            return ("A330", "A330neo", "Genis")
        if m.startswith("A330"):
            return ("A330", "A330ceo", "Genis")
        if m.startswith("A350"):
            return ("A350", "A350", "Genis")
        if m.startswith("A380"):
            return ("A380", "A380", "Genis")
        return None  # helikopterler (EC135, H160, AS332...) dışarıda
    return None


# Neo motor tipi (Airbus motor kodu): 27x = PW1100G (GTF), 25x = CFM LEAP-1A
def neo_engine(model: str):
    if not isinstance(model, str):
        return None
    mm = re.match(r"^A32[01](\d)(\d)\d", model.upper())
    if mm and model.upper().rstrip("X").endswith("N"):
        return {"7": "PW1100G (GTF)", "5": "CFM LEAP-1A"}.get(mm.group(2))
    return None


# Yolcu taşıyan ana hat (Part 121) işletmeciler — operatör kodu => isim
PAX_OPERATORS = {
    "AALA": "American", "DALA": "Delta", "CALA": "United", "SWAA": "Southwest",
    "ASAA": "Alaska", "YENA": "JetBlue", "GTIA": "Spirit", "HALA": "Hawaiian",
    "WX0A": "Allegiant", "SCNA": "Sun Country", "F3LA": "Frontier",
}
