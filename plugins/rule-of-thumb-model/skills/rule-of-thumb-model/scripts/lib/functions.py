"""The function catalogue: Excel and Google Sheets availability and the class allowed / flagged / refused
(design section 8.5; the readable list is references/functions.md, generated from this file).

Owner: CORE-F. Standard library only.

Classes (design 8.5, profile "both" unless named):
- refused: rule F-FUNC-REFUSED (error); build refuses a specification that uses the function.
- flagged: rule F-FUNC-FLAGGED (warning); a model accepts named flagged functions with settings.allow_functions.
  A flagged function that is volatile is reported once, as F-VOLATILE (lib.formula.check_formula).
- allowed: no finding.
- unknown (not in the catalogue): rule F-FUNC-UNKNOWN (warning).

Profiles: "both" (default) is the class above. "excel" and "sheets" move a function that was restricted only
because the other product lacks it to allowed for its own product (design 8.5); a function that does not exist
in the profile's product is refused there; a class that rests on modelling practice (FAST, books) stays.
Each entry stores its resolved class for all three profiles.

Evidence labels in the reasons: "confirmed" = checked against its source in research/verify/ (file and id
given); "not confirmed" = stated in a research report but not re-checked there, or the builder's reading of the
Microsoft and Google function lists. Writing the catalogue as markdown: run this file with --write.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

PRODUCTS = ("both", "excel", "sheets")
CLASSES = ("allowed", "flagged", "refused")
EXCEL_VALUES = ("yes", "365", "no")
SHEETS_VALUES = ("yes", "no", "not confirmed")

# Volatile functions (books B-56, confirmed: FMD p306 lists OFFSET, INDIRECT, RAND, NOW, TODAY; PwC p.7, local
# guides L-96, confirmed: OFFSET "will always recalculate"). RANDBETWEEN, RANDARRAY, CELL and INFO: builder's
# reading of Microsoft's list of volatile functions, not confirmed. FMD p306 also lists ROW and COLUMN; they are
# not treated as volatile here (Microsoft does not list them; not confirmed), because helper columns use ROW().
VOLATILE = frozenset({"NOW", "TODAY", "RAND", "RANDBETWEEN", "RANDARRAY", "OFFSET", "INDIRECT", "CELL", "INFO"})

# Volatile functions reported only as refused functions, not also as F-VOLATILE (design section 14).
VOLATILE_REPORTED_AS_REFUSED = frozenset({"OFFSET", "INDIRECT"})

# Functions Excel stores with the prefix "_xlfn." in the file: those added after Excel 2007 (the file format's
# "future functions"). Builder's reading of the Excel file format; not confirmed by research/verify. The Excel
# test in scripts/tests/test_formula.py checks a sample of them in Excel when Excel is available (passed in
# Excel 16.0 on the build PC on 2026-11-25 for XLOOKUP, XMATCH, MAXIFS, IFNA, CONCAT, IFS, LET, LAMBDA, MAP,
# FILTER, SORT; XLOOKUP without the prefix showed #NAME?).
XLFN_NAMES = frozenset("""
ACOT ACOTH AGGREGATE ANCHORARRAY ARABIC ARRAYTOTEXT BASE BETA.DIST BETA.INV BINOM.DIST BINOM.DIST.RANGE BINOM.INV
BITAND BITLSHIFT BITOR BITRSHIFT BITXOR BYCOL BYROW CEILING.MATH CEILING.PRECISE CHISQ.DIST CHISQ.DIST.RT
CHISQ.INV CHISQ.INV.RT CHISQ.TEST CHOOSECOLS CHOOSEROWS COMBINA CONCAT CONFIDENCE.NORM CONFIDENCE.T COT COTH
COVARIANCE.P COVARIANCE.S CSC CSCH DAYS DECIMAL DROP ECMA.CEILING ERF.PRECISE ERFC.PRECISE EXPAND EXPON.DIST
F.DIST F.DIST.RT F.INV F.INV.RT F.TEST FIELDVALUE FILTER FILTERXML FLOOR.MATH FLOOR.PRECISE FORECAST.ETS
FORECAST.ETS.CONFINT FORECAST.ETS.SEASONALITY FORECAST.ETS.STAT FORECAST.LINEAR FORMULATEXT GAMMA GAMMA.DIST
GAMMA.INV GAMMALN.PRECISE GAUSS GROUPBY HSTACK HYPGEOM.DIST IFNA IFS IMAGE IMCOSH IMCOT IMCSC IMCSCH IMSEC IMSECH
IMSINH IMTAN ISFORMULA ISO.CEILING ISOMITTED ISOWEEKNUM LAMBDA LET LOGNORM.DIST LOGNORM.INV MAKEARRAY MAP MAXIFS
MINIFS MODE.MULT MODE.SNGL MUNIT NEGBINOM.DIST NETWORKDAYS.INTL NORM.DIST NORM.INV NORM.S.DIST NORM.S.INV
NUMBERVALUE PDURATION PERCENTILE.EXC PERCENTILE.INC PERCENTOF PERCENTRANK.EXC PERCENTRANK.INC PERMUTATIONA PHI
PIVOTBY POISSON.DIST QUARTILE.EXC QUARTILE.INC QUERYSTRING RANDARRAY RANK.AVG RANK.EQ REDUCE REGEXEXTRACT
REGEXREPLACE REGEXTEST RRI SCAN SEC SECH SEQUENCE SHEET SHEETS SINGLE SKEW.P SORT SORTBY STDEV.P STDEV.S
STOCKHISTORY SWITCH T.DIST T.DIST.2T T.DIST.RT T.INV T.INV.2T T.TEST TAKE TEXTAFTER TEXTBEFORE TEXTJOIN TEXTSPLIT
TOCOL TOROW TRIMRANGE UNICHAR UNICODE UNIQUE VALUETOTEXT VAR.P VAR.S VSTACK WEBSERVICE WEIBULL.DIST WORKDAY.INTL
WRAPCOLS WRAPROWS XLOOKUP XMATCH XOR Z.TEST
""".split())

# Functions stored with "_xlfn._xlws." instead of "_xlfn." (same reading, not confirmed).
XLWS_NAMES = frozenset({"FILTER", "SORT"})


# Excel versions, newest first (v2, A-aera-19). Each entry lists the versions that have the function; compat's
# advice and references/functions.md are written from that list.
EXCEL_RELEASES = ("Microsoft 365", "Excel 2024", "Excel 2021", "Excel 2019", "Excel 2016", "Excel 2013", "Excel 2010")
V_ALL = EXCEL_RELEASES                  # in Excel 2010 or later
V_2013 = EXCEL_RELEASES[:6]             # Excel 2013 or later
V_2019 = EXCEL_RELEASES[:4]             # Excel 2019 or later
V_2021 = EXCEL_RELEASES[:3]             # Excel 2021 or later
V_2024 = EXCEL_RELEASES[:2]             # Excel 2024 and Microsoft 365
V_365 = EXCEL_RELEASES[:1]              # Microsoft 365 only


@dataclass(frozen=True)
class FunctionInfo:
    name: str                    # upper case, without _xlfn.
    excel: str                   # "yes" (Excel 2010 or later) | "365" (a recent Excel: see excel_versions) | "no"
    sheets: str                  # "yes" | "no" | "not confirmed"
    cls: str                     # class in profile "both": allowed | flagged | refused
    volatile: bool
    xlfn: bool                   # needs the _xlfn. prefix in the file
    reason: str                  # one line with its source
    replacement: str | None = None
    # Additions to the design's fields (CORE-F): the class in the two one-product profiles, and where the two
    # availabilities come from.
    cls_excel: str = ""
    cls_sheets: str = ""
    availability: str = ""
    # Addition in v2 (A-aera-19): the Excel versions that have the function, newest first (EXCEL_RELEASES);
    # () when Excel does not have it. The source of the list is in `availability`.
    excel_versions: tuple[str, ...] = ()
    # Addition in v4 (A-aera-20): how a function that both products have works differently in them ("" when it
    # works the same). The text is also part of `reason`; lib/compat.py reports the differing cases.
    differs: str = ""


# ----------------------------------------------------------------------------- sources used in the reasons

_V = "confirmed"
_N = "not confirmed"

AV_D3 = ("both: platforms D3 (the allowed set for both products) and A3.1 (Google function list); " + _N)
AV_K = "both: builder's reading of the Microsoft and Google function lists; " + _N
AV_K_2013 = "both; Excel 2013 and later: builder's reading of the Microsoft and Google function lists; " + _N
AV_K_2019 = "both; Excel 2019 and later: builder's reading of the Microsoft and Google function lists; " + _N
AV_A31_365 = ("Google: platforms A3.1 (Google function list), " + _N
              + "; Excel: Microsoft 365, Excel 2024 and Excel 2021: builder's reading of Microsoft's 'Applies to' "
              "lists, " + _N)
AV_A31_2019 = ("Google: platforms A3.1 (Google function list), " + _N
               + "; Excel: Microsoft 365 and Excel 2019 or later: builder's reading of Microsoft's 'Applies to' "
               "lists, " + _N)
AV_A31_2024 = ("Google: platforms A3.1 (Google function list), " + _N
               + "; Excel: Microsoft 365 and Excel 2024 only: builder's reading of Microsoft's 'Applies to' lists, "
               + _N)
# v8 (A-aera-15): the Excel statement of the LAMBDA helpers. The research synthesis lists "LAMBDA and its helpers
# MAP, BYROW, BYCOL, REDUCE, SCAN, MAKEARRAY" as Excel 365 and 2024 only and cites platforms A3.6; the claim check
# of A3.6 covers LET, LAMBDA and FILTER only, so the statement is not confirmed for these functions.
_EXCEL_LAMBDA_HELPER = ("Excel: Microsoft 365 and Excel 2024 only, like LAMBDA (research synthesis, which names "
                        "LAMBDA and its helpers and cites platforms A3.6; " + _N + " for this function: the claim "
                        "check of A3.6 covers LET, LAMBDA and FILTER only; builder's reading of Microsoft's 'Applies "
                        "to' lists, " + _N + ")")
AV_A31_LAMBDA_HELPER = ("Google: platforms A3.1 (Google function list), " + _N + "; " + _EXCEL_LAMBDA_HELPER)
AV_K_LAMBDA_HELPER = ("both; Google: builder's reading of Google's function list, " + _N + "; "
                      + _EXCEL_LAMBDA_HELPER)
AV_K_2021 = ("both; Excel: Microsoft 365, Excel 2024 and Excel 2021: builder's reading of the Microsoft and Google "
             "function lists; " + _N)
AV_A36_LET = ("Excel: Microsoft 365, 2024 and 2021 (platforms A3.6, " + _V + "); Google: platforms A3.1, " + _N)
AV_A36_LAMBDA = ("Excel: Microsoft 365 and 2024 only (platforms A3.6, " + _V + "); Google: platforms A3.1, " + _N)
AV_A36_FILTER = ("Excel: Microsoft 365, 2024, 2021 (platforms A3.6, " + _V + "); Google: platforms A3.1, " + _N)
AV_A35 = ("Excel: Microsoft 365 or 2024 (builder's reading, " + _N + "); Google: not shown in the fetched "
          "function list, which may be incomplete (platforms A3.5, " + _V + ")")
AV_A35_2021 = ("Excel: Microsoft 365, Excel 2024 and Excel 2021 (builder's reading, " + _N + "); Google: not shown "
               "in the fetched function list, which may be incomplete (platforms A3.5, " + _V + ")")
AV_A35_365 = ("Excel: Microsoft 365 only, not Excel 2024 (builder's reading, " + _N + "); Google: not shown in the "
              "fetched function list, which may be incomplete (platforms A3.5, " + _V + ")")
AV_A33 = ("Google: listed as a Sheets function (platforms A3.3, " + _V + " for this part; the check's status is "
          "'partly'); Excel: none (the cited source does not say so; builder's reading, " + _N + ")")
AV_GOOGLE_ONLY = ("Google only: builder's reading and the research synthesis D10 (inference); " + _N)
AV_R2_20 = ("Excel: yes; Google lists it as not supported in Sheets (researcher 2 #20, " + _V + ")")
AV_R2_20_CUBE = ("Excel: yes; Google lists 'CUBE functions' as not supported (researcher 2 #20, " + _V
                 + "; the check names CUBEKPIMEMBER, CUBEMEMBER and CUBEMEMBERPROPERTY; the other CUBE functions "
                 "are the builder's reading, " + _N + ")")
AV_K_NOT_SHEETS = ("Excel: yes; Google Sheets: no evidence found (builder's reading, " + _N + ")")
AV_365_NOT_SHEETS = ("Excel: Microsoft 365 (builder's reading, " + _N + "); Google Sheets: no evidence found ("
                     + _N + ")")
AV_2024_NOT_SHEETS = ("Excel: Microsoft 365 and Excel 2024 only (builder's reading, " + _N + "); Google Sheets: no "
                      "evidence found (" + _N + ")")
AV_LAMBDA_NOT_SHEETS = ("Excel: Microsoft 365 and Excel 2024 only, like LAMBDA (builder's reading of Microsoft's "
                        "'Applies to' lists, " + _N + "; no research item names this function); Google Sheets: no "
                        "evidence found (" + _N + ")")
AV_2021_NOT_SHEETS = ("Excel: Microsoft 365, Excel 2024 and Excel 2021, the versions with dynamic arrays "
                      "(builder's reading, " + _N + "); Google Sheets: no evidence found (" + _N + ")")

S_OFFSET = ("FAST 4.01-03 'Do not use OFFSET or INDIRECT functions' (platforms C1.48, " + _N + "); PwC p.7 "
            "highest risk: OFFSET, INDIRECT; OFFSET 'will always recalculate' (local guides L-96, " + _V + ")")
S_VOLATILE_FMD = "FMD p306 'Avoid volatile functions' (books B-56, " + _V + ")"
S_VOLATILE_K = "volatile: recalculates on every change (Microsoft's list; builder's reading, " + _N + ")"
S_LAMBDA = ("Excel has it only in Microsoft 365 and 2024 (platforms A3.6, " + _V + "); its logic sits inside one "
            "cell, where the procedure wants one step per helper cell (spec P3)")
# v8 (A-aera-15): the reason the plugin flags the LAMBDA helpers, apart from the availability statement.
S_LAMBDA_HELPER = ("takes a LAMBDA, so its logic sits inside one cell, where the procedure wants one step per "
                   "helper cell (spec P3)")
S_ISOMITTED = ("tests whether an argument of a LAMBDA was left out, so it is used only inside a LAMBDA, whose logic "
               "sits inside one cell, where the procedure wants one step per helper cell (spec P3)")
S_GOOGLE_ONLY = "Google Sheets only: the workbook must work in both products (spec 1.6, compatibility)"
S_GOOGLE_UNSUPPORTED = "not supported in Google Sheets (researcher 2 #20, " + _V + ")"
S_B25 = ("PFM p134 'VLOOKUP and HLOOKUP should generally be avoided', INDEX/MATCH preferred (books B-25, "
         + _V + ")")
S_CHOOSE = ("FAST 4.01-01 prefers INDEX over CHOOSE (platforms C1.48, " + _N + "; the section 4.01 preference is "
            "noted in platforms C1.39, " + _V + ")")
S_LET = ("keeps intermediate results inside one cell, where the procedure wants helper cells (spec P3; "
         "design 8.5)")
S_IFS = ("case selection inside one cell; FAST 3.03-06 prefers INDEX over IF to pick values and 3.03-07 says "
         "'Never use nested IFs' (platforms C1.39, " + _V + ")")
S_ARRAYS = ("spilling array: FAST 3.03-09 'Do not construct array formulas' (platforms C1.41, " + _N + "); "
            "spilled arrays are not supported inside Excel tables (platforms A3.7, " + _N + ")")
S_A35 = ("Sheets availability not confirmed: not in the fetched Google function list (platforms A3.5, "
         + _V + ")")
S_L98 = ("PwC p.7 lower risk: XNPV, NPV, IRR (local guides L-98, " + _V + "); the timing convention is hidden "
         "in the function (research synthesis D10)")
S_EXTERNAL_DATA = "brings data from outside the workbook into a formula; land it as a raw table instead (spec P2)"

R_HELPER = "helper cells or helper columns, one step per cell (helper-patterns.md)"
R_LAND = ("land the data as a raw table (column contract, then Power Query or Connected Sheets) and reference "
          "the raw sheet")
R_INDEX_MATCH = "INDEX(<return column>,MATCH(<key>,<key column>,0)), with the MATCH in its own helper cell"
R_LOOKUP_TABLE = "a lookup table read with INDEX and MATCH, or MIN/MAX or 1/0 flags in helper cells"
R_ARRAYS = "helper columns with 1/0 flags, then SUMIFS or COUNTIFS; or a list typed on the inputs sheet"
R_DATE_INPUT = "one dated input cell on the inputs sheet (the as-of date), referenced everywhere"
R_RANDOM = "scenario values typed on the inputs sheet, so every recalculation gives the same result"


# ----------------------------------------------------------------------------- catalogue

def _resolve(cls: str, excel: str, sheets: str, basis: str, cls_excel: str | None,
             cls_sheets: str | None) -> tuple[str, str]:
    """Class in profiles excel and sheets. basis "product": the profile-both class rests on the other product
    lacking the function (or Sheets availability not confirmed); "practice": it rests on modelling practice."""
    if excel == "no":
        ce = "refused"
    elif cls_excel is not None:
        ce = cls_excel
    else:
        ce = "allowed" if basis == "product" else cls
    if sheets == "no":
        cs = "refused"
    elif cls_sheets is not None:
        cs = cls_sheets
    elif basis == "product":
        cs = "allowed" if sheets == "yes" else "flagged"
    else:
        cs = cls if sheets == "yes" or cls != "allowed" else "flagged"
    return ce, cs


def _versions(name: str, excel: str, availability: str, versions: tuple[str, ...] | None) -> tuple[str, ...]:
    """The Excel versions of an entry: () when Excel lacks it; given explicitly for a "365" entry (each such
    function has its own list); else from Excel 2010 on, or from Excel 2013 on when the source says so."""
    if excel == "no":
        return ()
    if versions is not None:
        return tuple(versions)
    if excel == "365":  # pragma: no cover - a programming error in the catalogue
        raise RuntimeError(f"lib.functions: {name} needs its own list of Excel versions")
    return V_2013 if availability == AV_K_2013 else V_ALL


def _e(name: str, excel: str, sheets: str, cls: str, reason: str, replacement: str | None = None, *,
       availability: str = AV_K, basis: str = "practice", cls_excel: str | None = None,
       cls_sheets: str | None = None, versions: tuple[str, ...] | None = None, differs: str = "") -> FunctionInfo:
    ce, cs = _resolve(cls, excel, sheets, basis, cls_excel, cls_sheets)
    return FunctionInfo(name=name, excel=excel, sheets=sheets, cls=cls, volatile=name in VOLATILE,
                        xlfn=name in XLFN_NAMES, reason=reason, replacement=replacement, cls_excel=ce,
                        cls_sheets=cs, availability=availability,
                        excel_versions=_versions(name, excel, availability, versions), differs=differs)


def _allowed(names: str, reason: str, availability: str, excel: str = "yes",
             versions: tuple[str, ...] | None = None) -> list[FunctionInfo]:
    return [_e(n, excel, "yes", "allowed", reason, availability=availability, versions=versions)
            for n in names.split()]


_IN_BOTH_D3 = "in both products (platforms D3: allowed set for both products, " + _N + ")"
_IN_BOTH_DESIGN = "in both products (design 8.5 allowed list; builder's reading of both function lists, " + _N + ")"
_IN_BOTH_K = "in both products (builder's addition to design 8.5; builder's reading of both function lists, " + _N + ")"

# Functions that both products have but that work differently in them (FunctionInfo.differs; v4, A-aera-20).
# v5 (A-aera-13): exactly two single values is the shared form; one argument is reported and rewritten.
D_CONCAT = ("differs between the products: Excel's CONCAT takes any number of arguments and ranges; Google "
            "Sheets' CONCAT takes two values (builder's reading of the Microsoft and Google function pages, " + _N
            + "); exactly two single values is the form both products share; rotm.py compat reports a CONCAT "
            "with one argument, with more than two arguments or with a range argument (X-CONCAT, warning), and "
            "rewrites it with & when every argument is a single value (one argument a becomes a&\"\", the same "
            "text)")
# v6 (A-aera-9): FILTER's third argument has another meaning in each product.
D_FILTER = ("differs between the products in its third argument: in Excel the third argument of FILTER is the "
            "value returned when no row matches (Microsoft's FILTER page: FILTER returns an error on an empty "
            "result unless its third argument is given, platforms A3.7, " + _N + "); in Google Sheets FILTER "
            "takes a range and then conditions, FILTER(range, condition1, [condition2, ...]), so a third argument "
            "is a further condition (builder's reading of Google's FILTER page, " + _N + "); rotm.py compat "
            "reports a FILTER with a third argument at its cell (X-FILTER, warning) and does not rewrite it")

_ENTRIES: list[FunctionInfo] = [
    # --- refused in profile "both" --------------------------------------------------------------------------
    _e("OFFSET", "yes", "yes", "refused", "volatile, and its precedents are invisible to tracing; " + S_OFFSET,
       "INDEX over a whole column or a fixed range, e.g. INDEX(raw!$F:$F,<row>) (FAST 4.01-03)"),
    _e("INDIRECT", "yes", "yes", "refused", "volatile; a reference built from text hides its precedents; "
       + S_OFFSET, "a direct A1 reference, or INDEX and MATCH over a lookup table"),
    _e("LAMBDA", "365", "yes", "refused", S_LAMBDA, R_HELPER, availability=AV_A36_LAMBDA, versions=V_2024),
    _e("MAP", "365", "yes", "refused", S_LAMBDA_HELPER, R_HELPER, availability=AV_A31_LAMBDA_HELPER,
       versions=V_2024),
    _e("BYROW", "365", "yes", "refused", S_LAMBDA_HELPER, R_HELPER, availability=AV_A31_LAMBDA_HELPER,
       versions=V_2024),
    _e("BYCOL", "365", "yes", "refused", S_LAMBDA_HELPER, R_HELPER, availability=AV_K_LAMBDA_HELPER,
       versions=V_2024),
    _e("REDUCE", "365", "yes", "refused", S_LAMBDA_HELPER, R_HELPER, availability=AV_K_LAMBDA_HELPER,
       versions=V_2024),
    _e("SCAN", "365", "yes", "refused", S_LAMBDA_HELPER, R_HELPER, availability=AV_K_LAMBDA_HELPER,
       versions=V_2024),
    _e("MAKEARRAY", "365", "yes", "refused", S_LAMBDA_HELPER, R_HELPER, availability=AV_K_LAMBDA_HELPER,
       versions=V_2024),
    _e("ISOMITTED", "365", "not confirmed", "refused", S_ISOMITTED, R_HELPER,
       availability=AV_LAMBDA_NOT_SHEETS, versions=V_2024),
    # Google Sheets only (design 8.5); in profile "sheets" the practice reason decides the class.
    _e("QUERY", "no", "yes", "refused", S_GOOGLE_ONLY + "; in profile sheets "
       "flagged: one query text holds many steps (spec P3)",
       "SUMIFS or COUNTIFS over the raw columns, with helper columns for each step", availability=AV_A33,
       basis="product", cls_sheets="flagged"),
    _e("ARRAYFORMULA", "no", "yes", "refused", S_GOOGLE_ONLY + "; in profile sheets "
       "flagged: " + S_ARRAYS, "the same formula copied down a helper column", availability=AV_A33,
       basis="product", cls_sheets="flagged"),
    _e("IMPORTRANGE", "no", "yes", "refused", S_GOOGLE_ONLY + "; in profile sheets flagged: " + S_EXTERNAL_DATA,
       R_LAND, availability=AV_A33, basis="product", cls_sheets="flagged"),
    _e("IMPORTDATA", "no", "yes", "refused", S_GOOGLE_ONLY + "; in profile sheets flagged: " + S_EXTERNAL_DATA,
       R_LAND, availability=AV_GOOGLE_ONLY, basis="product", cls_sheets="flagged"),
    _e("IMPORTHTML", "no", "yes", "refused", S_GOOGLE_ONLY + "; in profile sheets flagged: " + S_EXTERNAL_DATA,
       R_LAND, availability=AV_GOOGLE_ONLY, basis="product", cls_sheets="flagged"),
    _e("IMPORTXML", "no", "yes", "refused", S_GOOGLE_ONLY + "; in profile sheets flagged: " + S_EXTERNAL_DATA,
       R_LAND, availability=AV_GOOGLE_ONLY, basis="product", cls_sheets="flagged"),
    _e("IMPORTFEED", "no", "yes", "refused", S_GOOGLE_ONLY + "; in profile sheets flagged: " + S_EXTERNAL_DATA,
       R_LAND, availability=AV_GOOGLE_ONLY, basis="product", cls_sheets="flagged"),
    _e("GOOGLEFINANCE", "no", "yes", "refused", S_GOOGLE_ONLY + "; in profile sheets flagged: "
       + S_EXTERNAL_DATA, "prices landed as a raw table, or typed on the inputs sheet with their date and source",
       availability=AV_GOOGLE_ONLY, basis="product", cls_sheets="flagged"),
    _e("SPARKLINE", "no", "yes", "refused", S_GOOGLE_ONLY + "; display only, allowed in profile sheets",
       "a chart outside the calculation sheets", availability=AV_GOOGLE_ONLY, basis="product"),
    _e("SPLIT", "no", "yes", "refused", S_GOOGLE_ONLY + "; allowed in profile sheets",
       "LEFT, MID and FIND in helper columns, one part per column",
       availability=("Google: Sheets has SPLIT (platforms A3.5 check note, " + _V + "); Excel: none (Excel has "
                     "TEXTSPLIT; builder's reading, " + _N + ")"), basis="product"),
    _e("JOIN", "no", "yes", "refused", S_GOOGLE_ONLY + "; allowed in profile sheets", "the & operator",
       availability=AV_GOOGLE_ONLY, basis="product"),
    _e("REGEXMATCH", "no", "yes", "refused", S_GOOGLE_ONLY + "; allowed in profile sheets",
       # v9 (A-aera-12): SEARCH ignores case and reads no regular expression; lib/compat.py gives the recipe only
       # for a typed pattern without a regular-expression sign (compat.regexmatch_fix)
       "ISNUMBER(SEARCH(<text>,<cell>)) in a helper column: it tests whether the cell holds the text, without "
       "regard to case, so it gives REGEXMATCH's result only for a pattern that is plain text",
       availability=AV_GOOGLE_ONLY, basis="product"),
    # Excel functions Google lists as not supported (researcher 2 #20, confirmed).
    _e("CALL", "yes", "no", "refused", S_GOOGLE_UNSUPPORTED + "; it calls code outside the workbook, refused "
       "in profile excel too", "none: remove it", availability=AV_R2_20, basis="product", cls_excel="refused"),
    _e("REGISTER.ID", "yes", "no", "refused", S_GOOGLE_UNSUPPORTED + "; it registers code outside the workbook, "
       "refused in profile excel too", "none: remove it", availability=AV_R2_20, basis="product",
       cls_excel="refused"),
    _e("INFO", "yes", "no", "refused", S_GOOGLE_UNSUPPORTED + "; volatile (builder's reading, " + _N + "); "
       "flagged in profile excel", "type the value on the inputs sheet", availability=AV_R2_20, basis="product",
       cls_excel="flagged"),
    _e("RTD", "yes", "no", "refused", S_GOOGLE_UNSUPPORTED + "; in profile excel flagged: " + S_EXTERNAL_DATA,
       R_LAND, availability=AV_R2_20, basis="product", cls_excel="flagged"),
    _e("WEBSERVICE", "yes", "no", "refused", S_GOOGLE_UNSUPPORTED + "; in profile excel flagged: "
       + S_EXTERNAL_DATA, R_LAND, availability=AV_R2_20, basis="product", cls_excel="flagged"),
    *[_e(n, "yes", "no", "refused", S_GOOGLE_UNSUPPORTED + "; in profile excel flagged: " + S_EXTERNAL_DATA,
         "land the cube result as a raw table through Power Query", availability=AV_R2_20_CUBE,
         basis="product", cls_excel="flagged")
      for n in ("CUBEKPIMEMBER", "CUBEMEMBER", "CUBEMEMBERPROPERTY", "CUBERANKEDMEMBER", "CUBESET",
                "CUBESETCOUNT", "CUBEVALUE")],

    # --- flagged in profile "both" --------------------------------------------------------------------------
    _e("VLOOKUP", "yes", "yes", "flagged", S_B25 + "; the typed column number is hard to check", R_INDEX_MATCH),
    _e("HLOOKUP", "yes", "yes", "flagged", S_B25 + "; the typed row number is hard to check", R_INDEX_MATCH),
    _e("CHOOSE", "yes", "yes", "flagged", S_CHOOSE, "INDEX over a range that holds the options"),
    _e("LET", "365", "yes", "flagged", S_LET + "; Excel 365, 2024 and 2021 (platforms A3.6, " + _V + ")",
       "helper cells, one named step per cell", availability=AV_A36_LET, versions=V_2021),
    _e("IFS", "365", "yes", "flagged", S_IFS, R_LOOKUP_TABLE, availability=AV_A31_2019, versions=V_2019),
    _e("SWITCH", "365", "yes", "flagged", S_IFS, R_LOOKUP_TABLE, availability=AV_A31_2019, versions=V_2019),
    _e("FILTER", "365", "yes", "flagged", S_ARRAYS + "; " + D_FILTER, R_ARRAYS, availability=AV_A36_FILTER,
       versions=V_2021, differs=D_FILTER),
    _e("UNIQUE", "365", "yes", "flagged", S_ARRAYS, R_ARRAYS, availability=AV_A31_365, versions=V_2021),
    _e("SORT", "365", "yes", "flagged", S_ARRAYS, R_ARRAYS, availability=AV_A31_365, versions=V_2021),
    _e("SEQUENCE", "365", "yes", "flagged", S_ARRAYS, "a counter column: =N(<cell above>)+1 in a helper column",
       availability=AV_A31_365, versions=V_2021),
    _e("SORTBY", "365", "not confirmed", "flagged", S_ARRAYS + "; " + S_A35, R_ARRAYS, availability=AV_A35_2021,
       versions=V_2021),
    _e("TAKE", "365", "not confirmed", "flagged", S_A35 + "; " + S_ARRAYS,
       "INDEX with a row counter in a helper column", availability=AV_A35, versions=V_2024),
    _e("DROP", "365", "not confirmed", "flagged", S_A35 + "; " + S_ARRAYS,
       "INDEX with a row counter in a helper column", availability=AV_A35, versions=V_2024),
    _e("TEXTSPLIT", "365", "not confirmed", "flagged", S_A35 + "; " + S_ARRAYS,
       "LEFT, MID and FIND in helper columns, one part per column", availability=AV_A35, versions=V_2024),
    _e("GROUPBY", "365", "not confirmed", "flagged", S_A35 + "; " + S_ARRAYS,
       "a SUMIFS grid with the groups typed or linked as row labels", availability=AV_A35_365, versions=V_365),
    _e("PIVOTBY", "365", "not confirmed", "flagged", S_A35 + "; " + S_ARRAYS,
       "a SUMIFS grid with row labels and column headers", availability=AV_A35_365, versions=V_365),
    _e("TRIMRANGE", "365", "not confirmed", "flagged", S_A35 + "; allowed in profile excel",
       "a whole-column reference, e.g. raw!$F:$F", availability=AV_A35_365, basis="product", versions=V_365),
    *[_e(n, "365", "yes", "flagged", S_ARRAYS, R_ARRAYS, availability=AV_A31_2024, versions=V_2024)
      for n in ("CHOOSECOLS", "VSTACK", "HSTACK", "TOCOL")],
    *[_e(n, "365", "not confirmed", "flagged", S_ARRAYS, R_ARRAYS, availability=AV_2024_NOT_SHEETS,
         versions=V_2024)
      for n in ("CHOOSEROWS", "TOROW", "WRAPROWS", "WRAPCOLS", "EXPAND")],
    _e("TODAY", "yes", "yes", "flagged", S_VOLATILE_FMD + "; results change from day to day", R_DATE_INPUT),
    _e("NOW", "yes", "yes", "flagged", S_VOLATILE_FMD + "; results change on every recalculation", R_DATE_INPUT),
    _e("RAND", "yes", "yes", "flagged", S_VOLATILE_FMD + "; checks cannot be repeated", R_RANDOM),
    _e("RANDBETWEEN", "yes", "yes", "flagged", S_VOLATILE_K + "; checks cannot be repeated", R_RANDOM),
    _e("RANDARRAY", "365", "yes", "flagged", S_VOLATILE_K + "; " + S_ARRAYS, R_RANDOM, availability=AV_K_2021,
       versions=V_2021),
    _e("CELL", "yes", "yes", "flagged", S_VOLATILE_K + "; often used to read a sheet name",
       "type the sheet name or the value on the inputs sheet"),
    _e("NPV", "yes", "yes", "flagged", S_L98, "discount factors in a helper row, then SUMPRODUCT"),
    _e("XNPV", "yes", "yes", "flagged", S_L98, "discount factors from dates in a helper row, then SUMPRODUCT"),
    _e("IRR", "yes", "yes", "flagged", S_L98, "show the cash flows and the discount factors in helper rows; "
       "allow IRR for the model with settings.allow_functions when it is the reported output"),
    _e("XIRR", "yes", "yes", "flagged", S_L98, "show the dated cash flows in helper rows; allow XIRR for the "
       "model with settings.allow_functions when it is the reported output"),
    _e("FILTERXML", "yes", "not confirmed", "flagged", "Sheets availability not confirmed; allowed in profile "
       "excel", "LEFT, MID and FIND in helper columns", availability=AV_K_NOT_SHEETS, basis="product"),
    _e("SINGLE", "365", "not confirmed", "flagged", "the file form of the @ implicit-intersection operator "
       "(builder's reading, " + _N + "); allowed in profile excel",
       "a single-cell reference", availability=AV_2021_NOT_SHEETS, basis="product", versions=V_2021),
    _e("ANCHORARRAY", "365", "not confirmed", "flagged", "the file form of a spill reference A1# (builder's "
       "reading, " + _N + "); " + S_ARRAYS, "reference a fixed range, or build the list in helper cells",
       availability=AV_2021_NOT_SHEETS, versions=V_2021),

    # --- allowed ---------------------------------------------------------------------------------------------
    *_allowed("SUM SUMIFS COUNTIFS AVERAGEIFS INDEX MATCH IF IFERROR AND OR MIN MAX ABS EOMONTH EDATE YEAR "
              "MONTH DATE TEXT", _IN_BOTH_D3, AV_D3),
    _e("ROUND", "yes", "yes", "allowed", _IN_BOTH_D3 + "; use only where needed: PwC lists the ROUND family as "
       "lower risk, especially in checks (local guides L-98, " + _V + ")", availability=AV_D3),
    _e("XLOOKUP", "365", "yes", "allowed", _IN_BOTH_D3 + "; Excel 2021 or Microsoft 365; the oldest Excel version "
       "with it is not confirmed (platforms E4)", availability=AV_A31_365, versions=V_2021),
    _e("XMATCH", "365", "yes", "allowed", _IN_BOTH_D3 + "; Google has an XMATCH help page (platforms A3.5 check "
       "note, " + _V + ")", availability=("Google: XMATCH help page exists (platforms A3.5, " + _V + "); Excel: "
                                          "Microsoft 365, Excel 2024 and Excel 2021: builder's reading, " + _N),
       versions=V_2021),
    *_allowed("COUNT COUNTA COUNTIF SUMIF SUMPRODUCT ROW ROWS COLUMN COLUMNS NOT ISBLANK ISNUMBER ISTEXT ISERROR LEN LEFT "
              "RIGHT MID TRIM UPPER LOWER VALUE N NA DAY AVERAGE", _IN_BOTH_DESIGN, AV_K),
    _e("IFNA", "yes", "yes", "allowed", _IN_BOTH_DESIGN + "; PwC: 'Always test for the specific error, e.g., ISNA' "
       "(local guides L-98, " + _V + ")", availability=AV_K_2013),
    *_allowed("MAXIFS MINIFS", _IN_BOTH_DESIGN, AV_K_2019, excel="365", versions=V_2019),
    *_allowed("AVERAGEIF COUNTBLANK MOD INT ROUNDUP ROUNDDOWN MROUND CEILING FLOOR TRUNC SIGN PRODUCT POWER SQRT "
              "EXP LN MEDIAN LARGE "
              "SMALL SUBTOTAL ISNA ISERR ISLOGICAL EXACT FIND SEARCH SUBSTITUTE REPLACE REPT PROPER WEEKDAY "
              "NETWORKDAYS WORKDAY YEARFRAC TRUE FALSE PMT PV FV NPER RATE", _IN_BOTH_K, AV_K),
    *_allowed("DAYS XOR", _IN_BOTH_K, AV_K_2013),
    # DATEVALUE and TIMEVALUE: added after live test 1 (2026-11-25), where DATEVALUE got F-FUNC-UNKNOWN and the
    # agent fell back on EOMONTH reading a text date
    *_allowed("DATEVALUE TIMEVALUE", _IN_BOTH_K, AV_K),
    # v4 (A-aera-20): CONCAT exists in both products with different arguments
    _e("CONCAT", "365", "yes", "allowed", _IN_BOTH_K + "; " + D_CONCAT,
       'the & operator for single values; TEXTJOIN("",FALSE,<range>) for a range', availability=AV_K_2019,
       versions=V_2019, differs=D_CONCAT),
    *_allowed("TEXTJOIN", _IN_BOTH_K, AV_K_2019, excel="365", versions=V_2019),
    _e("CONCATENATE", "yes", "yes", "allowed", _IN_BOTH_K + "; rotm.py compat may rewrite it with the & operator "
       "(design 15)", "the & operator", availability=AV_K),
]

CATALOGUE: dict[str, FunctionInfo] = {e.name: e for e in _ENTRIES}
if len(CATALOGUE) != len(_ENTRIES):  # pragma: no cover - a duplicate name is a programming error
    raise RuntimeError("lib.functions: duplicate catalogue entry")


# ----------------------------------------------------------------------------- lookups

def normalize_name(name: str) -> str:
    """Upper case, without the file prefixes _xlfn., _xlfn._xlws., _xlws. and a trailing '('."""
    n = name.strip()
    if n.endswith("("):
        n = n[:-1]
    low = n.lower()
    for prefix in ("_xlfn._xlws.", "_xlfn.", "_xlws."):
        if low.startswith(prefix):
            n = n[len(prefix):]
            break
    return n.upper()


def info(name: str) -> FunctionInfo | None:
    """The entry for a function name (case-insensitive, _xlfn. ignored), None when not in the catalogue."""
    return CATALOGUE.get(normalize_name(name))


def _check_product(product: str) -> None:
    if product not in PRODUCTS:
        raise ValueError(f"product must be one of {', '.join(PRODUCTS)}; got {product!r}")


def classify(name: str, product: str = "both") -> str:
    """allowed | flagged | refused | unknown for the product profile."""
    _check_product(product)
    entry = info(name)
    if entry is None:
        return "unknown"
    if product == "excel":
        return entry.cls_excel
    if product == "sheets":
        return entry.cls_sheets
    return entry.cls


def needs_xlfn(name: str) -> bool:
    """True when Excel stores the function with the _xlfn. prefix in the file (any function, catalogued or not)."""
    return normalize_name(name) in XLFN_NAMES


def file_prefix(name: str) -> str:
    """The prefix Excel stores before the function name in the file: '', '_xlfn.' or '_xlfn._xlws.'."""
    n = normalize_name(name)
    if n in XLWS_NAMES:
        return "_xlfn._xlws."
    return "_xlfn." if n in XLFN_NAMES else ""


def replacement(name: str) -> str | None:
    entry = info(name)
    return entry.replacement if entry else None


def versions_text(versions: tuple[str, ...], conjunction: str = "and") -> str:
    """A version list as words: ("Microsoft 365", "Excel 2024") -> "Microsoft 365 and Excel 2024" (or "or");
    four or more versions are written as a range: "Microsoft 365 and Excel 2019 to Excel 2024"."""
    if not versions:
        return "no Excel version"
    if len(versions) >= 4:
        return f"{versions[0]} {conjunction} {versions[-1]} to {versions[1]}"
    if len(versions) == 1:
        return versions[0]
    return ", ".join(versions[:-1]) + f" {conjunction} {versions[-1]}"


def names(cls: str | None = None, product: str = "both") -> list[str]:
    """Catalogue names, in catalogue order, optionally only those of one class in one profile."""
    _check_product(product)
    if cls is None:
        return list(CATALOGUE)
    return [n for n in CATALOGUE if classify(n, product) == cls]


def sheets_only_names() -> list[str]:
    """The functions only Google Sheets has, in catalogue order: Excel "no" and Sheets "yes" (v7, r6 verdict
    A-aera-8). Excel cannot calculate them, so `rotm.py check` does not open a workbook that calls one in Excel
    (X-SHEETS-ONLY, lib/check.py); the alternative of each is its `replacement`."""
    return [n for n, e in CATALOGUE.items() if e.excel == "no" and e.sheets == "yes"]


# ----------------------------------------------------------------------------- references/functions.md

def _cell(text: str | None) -> str:
    return (text or "-").replace("|", "\\|").replace("\n", " ")


def _versions_cell(versions: tuple[str, ...]) -> str:
    """The Excel versions column: each version when there are three or fewer, else the newest and the range."""
    if not versions:
        return "-"
    if len(versions) <= 3:
        return ", ".join(versions)
    return f"{versions[0]}, {versions[-1]} to {versions[1]}"


def lift_functions() -> frozenset[str]:
    """The functions the Python script of `rotm.py lift` reproduces: lib.lift.supported_functions(), the one list
    (v3, A-aera-13). Imported here only when the catalogue is written (lib.lift is standard library only too)."""
    from . import lift
    return lift.supported_functions()


def _lift_section() -> list[str]:
    from . import lift
    reproduced = lift_functions()
    in_catalogue = [n for n in CATALOGUE if n in reproduced]
    others = sorted(reproduced - set(CATALOGUE))
    out = [
        "## The Lift column",
        "",
        "- `yes`: the Python script that `rotm.py lift` writes reproduces the function. The column is generated from "
        "`lib.lift.supported_functions()`, the one list the lift evaluator keeps.",
        "- `no`: `rotm.py lift` reports `LIFT-UNSUPPORTED` (error, exit 1) for a model that uses the function, before "
        "the script is written, with the number of cells and the first cell. The script shows those cells as not "
        "reproduced.",
        f"- Lift reproduces {len(in_catalogue)} of the {len(CATALOGUE)} functions in the catalogue.",
    ]
    out += [f"- {n}: {lift.REPRODUCED_LIMITS[n]}." for n in sorted(lift.REPRODUCED_LIMITS)]
    if others:
        out.append("- Lift also reproduces these functions, which are not in the catalogue (a model that uses one "
                   "gets F-FUNC-UNKNOWN): " + ", ".join(others) + ".")
    return out + [""]


def render_markdown() -> str:
    """The text of references/functions.md (test_functions checks that the file equals this)."""
    counts = {c: len(names(c)) for c in CLASSES}
    added = sum(1 for e in CATALOGUE.values() if e.reason.startswith(_IN_BOTH_K))
    lifted = lift_functions()
    out: list[str] = [
        "# Function catalogue",
        "",
        "Generated from `scripts/lib/functions.py`. After editing the catalogue there, run "
        "`python -P -X utf8 -B scripts/lib/functions.py --write` from the skill folder. The audit and the build "
        "read the catalogue in the code, not this file.",
        "",
        f"- Functions in the catalogue: {len(CATALOGUE)} ({counts['refused']} refused, {counts['flagged']} "
        f"flagged, {counts['allowed']} allowed in profile `both`).",
        "- The nesting depth of a formula counts every function call, whatever its class (design 8.1).",
        "",
        "## How the classes are used",
        "",
        "| Class | Rule | Severity | Effect |",
        "| --- | --- | --- | --- |",
        "| refused | F-FUNC-REFUSED | error | `build` refuses the specification (exit 2); `audit` reports an "
        "error. |",
        "| flagged | F-FUNC-FLAGGED | warning | `--strict` exits 1. A model accepts named flagged functions with "
        "`settings.allow_functions` in its specification. |",
        "| allowed | - | - | no finding |",
        "| not in the catalogue | F-FUNC-UNKNOWN | warning | check that the function exists in both products |",
        "",
        "- Volatile functions (TODAY, NOW, RAND, RANDBETWEEN, RANDARRAY, CELL, INFO) are reported as F-VOLATILE "
        "(warning). A flagged volatile function is reported once, as F-VOLATILE, not also as F-FUNC-FLAGGED.",
        "- OFFSET and INDIRECT are volatile too; they are reported only as F-FUNC-REFUSED (design 14).",
        "- `settings.allow_functions` silences F-FUNC-FLAGGED and F-VOLATILE for the functions it names. It does "
        "not change a refused function.",
        "",
        "## Profiles",
        "",
        "- `both` (the default): the workbook must work in Excel and in Google Sheets.",
        "- `excel` and `sheets`: a function that is restricted only because the other product lacks it is "
        "allowed in its own product (design 8.5). A function that does not exist in the profile's product is "
        "refused there. A class that rests on modelling practice (FAST, the books) stays the same in every "
        "profile.",
        "- The profile is the specification's `product` key, or `--product` on `audit`, `compat` and `formula`.",
        # v7 (r6 verdict A-aera-8, plan T-21): what is verified for a model with a function only Google Sheets has
        "- A model that uses a function only Google Sheets has (JOIN, SPLIT, REGEXMATCH and the others this "
        "catalogue marks Excel \"no\") cannot be verified in Excel: build prints I-SHEETS-ONLY, and `rotm.py check` "
        "reports X-SHEETS-ONLY, does not open the workbook in Excel, leaves the file byte for byte and exits 3 "
        "(\"audited, not recalculated: functions only Google Sheets has\"). Prefer the alternative the catalogue "
        "names (for JOIN the & operator). Such a model is listed under \"Not verified\".",
        "- The functions only Google Sheets has (Excel `no`, Sheets `yes`): " + ", ".join(sheets_only_names()) + ".",
        "",
        "## Evidence labels",
        "",
        "- \"confirmed\": the claim was checked against its source in `research/verify/` (the id names the "
        "item, for example platforms A3.6 or local guides L-96).",
        "- \"not confirmed\": the claim is stated in a research report but was not re-checked there, or it is "
        "the builder's reading of the Microsoft and Google function lists.",
        "- Excel column: `yes` = in Excel 2010 or later (the availability text names a later first version where "
        "one applies); `365` = needs a recent Excel (the Excel versions column lists the versions that have it); "
        "`no` = not in Excel.",
        "- Excel versions column: the versions that have the function, newest first. `rotm.py compat` writes its "
        "advice about Excel versions from this list (for example LAMBDA: Microsoft 365 and Excel 2024, not "
        "Excel 2021). The source of each list is in the availability column.",
        "- Sheets column: `yes`, `no`, or `not confirmed` (no evidence that Google Sheets has it).",
        "",
    ]
    out += _lift_section()
    titles = {"refused": "Refused (profile both)", "flagged": "Flagged (profile both)",
              "allowed": "Allowed (profile both)"}
    for cls in ("refused", "flagged", "allowed"):
        out += [f"## {titles[cls]}", "",
                "| Function | Excel | Sheets | Excel versions | Class in profile excel / sheets | Volatile | Lift | "
                "File prefix | Reason and source | Use instead | Availability and source |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for n in names(cls):
            e = CATALOGUE[n]
            out.append(f"| {n} | {e.excel} | {e.sheets} | {_versions_cell(e.excel_versions)} | "
                       f"{e.cls_excel} / {e.cls_sheets} | "
                       f"{'yes' if e.volatile else 'no'} | {'yes' if n in lifted else 'no'} | "
                       f"{_cell(file_prefix(n) or None)} | {_cell(e.reason)} | "
                       f"{_cell(e.replacement)} | {_cell(e.availability)} |")
        out.append("")
    out += [
        "## Functions that differ between the products",
        "",
        "- A function in this list exists in Excel and in Google Sheets under the same name, but takes other "
        "arguments in one of them. Its class does not change; `rotm.py compat` reports the cases that differ.",
    ]
    out += [f"- {n}: {e.differs}." for n, e in CATALOGUE.items() if e.differs]
    out += [
        "",
        "## The file prefixes `_xlfn.`, `_xlfn._xlws.` and `_xlpm.`",
        "",
        "- Excel stores a function added after Excel 2007 with the prefix `_xlfn.` in the file, FILTER and SORT "
        "with `_xlfn._xlws.`, and the names that LET and LAMBDA define with `_xlpm.`. A spill reference `A1#` is "
        "stored as `_xlfn.ANCHORARRAY(A1)` and the `@` operator as `_xlfn.SINGLE(...)`; the formula rules show it "
        "as `@<argument>` (`@(<argument>)` for an expression) and do not count it in the nesting depth.",
        "- openpyxl writes the formula text it is given. `build` passes every formula through "
        "`formula.to_file_formula()`, which adds these prefixes (design 6.6).",
        "- `formula.analyze()` and the audit work on the text the formula bar shows: `formula.strip_xlfn()` "
        "removes the prefixes first, so a stored `_xlfn.ANCHORARRAY(A1)` is measured as `A1#` (depth 0), not as "
        "a function call.",
        "- The list of prefixed functions is the builder's reading of the Excel file format (not confirmed by "
        "`research/verify/`).",
        "- Verified in Excel 16.0 on the build PC on 2026-11-25 (test `test_file_prefixes_recalculate_in_excel` "
        "in `scripts/tests/test_formula.py`): 11 formulas written by openpyxl in the form `to_file_formula()` "
        "gives (XLOOKUP, XMATCH, MAXIFS, IFNA, CONCAT, IFS, LET, LAMBDA, MAP with LAMBDA, FILTER and SORT) "
        "recalculated to the expected values, and `=XLOOKUP(...)` written without the prefix showed `#NAME?`.",
        "- Not verified: the other prefixed functions; whether Excel also accepts LET and LAMBDA names without "
        "`_xlpm.`; spill references and `@` written by openpyxl. The Excel test runs only where Excel is "
        "available.",
        "",
        "## Volatile functions",
        "",
        "- FMD p306 lists OFFSET, INDIRECT, RAND, NOW, TODAY, ROW and COLUMN as volatile (books B-56, confirmed).",
        "- The plugin does not treat ROW and COLUMN as volatile: Microsoft's list of volatile functions does not "
        "include them (builder's reading, not confirmed), and helper columns use `=ROW()` (design D8).",
        "- RANDBETWEEN, RANDARRAY, CELL and INFO are volatile in Microsoft's list (builder's reading, not "
        "confirmed).",
        "",
        "## Choices for Max",
        "",
        "- IFERROR, IFNA and ISERROR are allowed (design 8.5). The research synthesis (D10) proposes flagging "
        "IFERROR, IFNA, ISERROR and ISERR, because PwC lists them as lower risk and says \"Always test for the "
        "specific error, e.g., ISNA\" (local guides L-98, confirmed). Changing IFERROR to flagged would make "
        "every IFERROR a warning.",
        "- RAND, RANDBETWEEN and CELL are flagged (design 8.5). The research synthesis proposes refusing them.",
        "- NPV, IRR, XNPV and XIRR are flagged (research synthesis D10; local guides L-98, confirmed). The design "
        "did not list them; a model that reports an IRR allows it with `settings.allow_functions`.",
        f"- {added} common functions (for example MOD, INT, ROUNDUP, SUBSTITUTE, WEEKDAY, PMT) are allowed as the "
        "builder's addition to the design's allowed list. Their availability in both products is the builder's "
        "reading of the vendors' lists (not confirmed). Without them each would be an F-FUNC-UNKNOWN warning.",
        "",
    ]
    return "\n".join(out)


def markdown_path() -> Path:
    return Path(__file__).resolve().parents[2] / "references" / "functions.md"


def write_markdown(path: str | Path | None = None) -> Path:
    target = Path(path) if path else markdown_path()
    target.write_text(render_markdown(), encoding="utf-8", newline="\n")
    return target


if __name__ == "__main__":  # python -P -X utf8 -B scripts/lib/functions.py --write
    # Imported again as lib.functions, so that it can read the Lift column from lib.lift (a relative import).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lib import functions as _catalogue
    if sys.argv[1:] == ["--write"]:
        print(f"wrote {_catalogue.write_markdown().as_posix()}")
    else:
        sys.stdout.write(_catalogue.render_markdown())
