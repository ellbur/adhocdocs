"""Names in document text: what counts as a person's name, and why."""

import pytest

from adhocdocs import analysis
from adhocdocs.names import mentions


def found(text):
    return {(m.form, tuple(m.basis), m.role) for m in mentions(text)}


@pytest.mark.parametrize("text, form, basis, role", [
    ("On 2/24 José Ibáñez, a dock associate, signed it.", "José Ibáñez", ("role",), "dock associate"),
    ("picked up by driver R. Ames at 10:20", "R. Ames", ("initial", "role"), "driver"),
    ("told Dana that the driver,\nD. Pruitt, remembers", "D. Pruitt", ("initial", "role"), "driver"),
    ("Questions: Raymond Voss, Controller.", "Raymond Voss", ("role",), "controller"),
    ("Tom Kettering (our receiving supervisor) did not sign", "Tom Kettering", ("role",), "receiving supervisor"),
    ("Dear Mr. Delacroix-Hale:", "Mr. Delacroix-Hale", ("title",), None),
    ("BY MS. STROUD:", "MS. STROUD", ("title", "by"), None),
    ("From: Evelyn Stroud <estroud@pellhamstroud.example>", "Evelyn Stroud", ("address", "by"), None),
    ("By: /s/ Raymond Voss", "Raymond Voss", ("by",), None),
    ("I, Linnea Fairbrother, Registered Professional\nReporter, certify", "Linnea Fairbrother", ("self", "role"),
     "registered professional reporter"),
    ("Very truly yours,\nEvelyn Stroud\n", "Evelyn Stroud", ("signature",), None),
    ("Sincerely,\n- Gary Lindqvist\nClaims Manager\n", "Gary Lindqvist", ("signature", "role"), "claims manager"),
    ("EVELYN STROUD, ESQ.\nPellham & Stroud LLP", "EVELYN STROUD", ("esq",), "attorney"),
    ("José Ibáñez\nDock Associate (Dock 4)\n", "José Ibáñez", ("role",), "dock associate"),
    ("No comment from Mr. Welch’s office.", "Mr. Welch", ("title",), None),
    ("That's José Ibáñez?", "José Ibáñez", ("name",), None),
])
def test_found(text, form, basis, role):
    assert (form, basis, role) in found(text)


@pytest.mark.parametrize("text", [
    "Q. Good morning, Mr. Kettering.",                  # Q. is not an initial
    "U.S. Customs and Border Protection",               # S. Customs
    "Lamex Foods, 2017 WL 4334064 (D. Colo. Mar. 8, 2017); 520 F. Supp. 2d 1",
    "I. Background\n",                                   # an outline heading
    "Exel, Inc. v. S. Refrigerated Transp., Inc.",
    "acting lead PM",                                    # one capitalized word, not a name
    "TERMS PREPAID\nDRIVER. : D. PRUITT",               # a heading is not a driver
    "Bellwether Procurement",
    "the brown pallet; Will Brown",                     # brown is a word here
])
def test_not_found(text):
    forms = {m.form for m in mentions(text)}
    assert not any(f in forms for f in ("Q. Good", "S. Customs", "D. Colo", "F. Supp", "I. Background",
                                        "S. Refrigerated Transp", "PM", "Bellwether Procurement",
                                        "Will Brown"))
    assert not any(m.form == "TERMS PREPAID" and m.basis != ["name"] for m in mentions(text))


def test_names_are_spans_of_the_text():
    text = "per TK email; requested by Z. Müller, see RCV-26-0224."
    [m] = [m for m in mentions(text) if m.surname == "muller"]
    assert text[m.start:m.end] == "Z. Müller" and m.initial == "z" and m.given is None


def test_given_names():
    assert analysis.same_given("tom", "thomas") and analysis.same_given("dan", "daniel")
    assert not analysis.same_given("jane", "john") and not analysis.same_given("al", "alexander")


def test_addresses_are_not_words():
    assert ("Zoe Muller", ("name",), None) in found("Cc: zoe@work.example\n\nZoe Muller here.")


def test_last_first_before_an_address():
    [m] = [m for m in mentions('From: "Muller, Zoe" <zmuller@home.example>') if m.surname == "muller"]
    assert (m.form, m.given, m.basis) == ("Muller, Zoe", "zoe", ["address"])
