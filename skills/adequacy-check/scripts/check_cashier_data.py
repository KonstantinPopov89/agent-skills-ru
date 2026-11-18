#!/usr/bin/env python3
"""Deterministic, contract-driven checks of observed cashier aggregates (stdlib only)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
from pathlib import Path

VERSION = "1.0.0"
ROLES = ("date", "account", "gross", "returns", "net", "receipts")
NUMERIC = ROLES[2:]
MISSING = object()
NUMBER = re.compile(r"[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
STATUSES = ("PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE")


class InvalidInput(ValueError):
    """Error messages must be fixed text, never source values or paths."""


def decimal_text(value):
    if value is None:
        return None
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if value == 0 else result


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidInput("JSON object contains a duplicate field name")
        result[key] = value
    return result


def decode_json(data, allow_nonfinite=True):
    def constant(value):
        if not allow_nonfinite:
            raise InvalidInput("Non-finite constants are not valid in a specification")
        return Decimal(value)
    try:
        return json.loads(data.decode("utf-8-sig"), parse_float=Decimal,
                          parse_int=Decimal, parse_constant=constant,
                          object_pairs_hook=pairs_no_duplicates)
    except (UnicodeError, json.JSONDecodeError, InvalidOperation, RecursionError):
        raise InvalidInput("Input is not supported UTF-8 JSON") from None


def keys(obj, allowed, required=()):
    if not isinstance(obj, dict) or set(obj) - set(allowed) or set(required) - set(obj):
        raise InvalidInput("Specification has an invalid object shape or unsupported option")


def text_nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def evidence(value):
    return isinstance(value, list) and bool(value) and all(text_nonempty(x) for x in value)


def parse_number(raw):
    """Return value, status, safe reason; no locale guesses, coercion, or fill-zero."""
    if raw is MISSING:
        return None, "UNKNOWN", "missing_field"
    if raw is None or raw == "":
        return None, "UNKNOWN", "null_or_empty"
    if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
        return None, "FAIL", "not_a_numeric_scalar"
    if isinstance(raw, str):
        if raw in ("NaN", "Infinity", "-Infinity", "+Infinity"):
            return None, "UNKNOWN", "non_finite"
        if not NUMBER.fullmatch(raw):
            return None, "FAIL", "invalid_numeric_syntax"
        if len(raw) > 1300:
            return None, "FAIL", "numeric_resource_limit"
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        return None, "FAIL", "invalid_numeric_syntax"
    if not number.is_finite():
        return None, "UNKNOWN", "non_finite"
    t = number.as_tuple()
    if len(t.digits) > 256 or abs(t.exponent) > 1000 or abs(number.adjusted()) > 1000:
        return None, "FAIL", "numeric_resource_limit"
    return number, "PASS", "finite_decimal"


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise InvalidInput("Specification period dates must be ISO calendar dates")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidInput("Specification contains an invalid period date") from None


def validate_spec(spec):
    allowed = {"schema_version", "input", "source", "assumptions", "scope", "granularity",
               "fields", "date_format", "money", "period", "include_accounts", "contract",
               "mean_decimal_places"}
    required = allowed - {"include_accounts", "mean_decimal_places"}
    keys(spec, allowed, required)
    if isinstance(spec["schema_version"], bool) or spec["schema_version"] != 1:
        raise InvalidInput("Unsupported specification version")
    keys(spec["input"], {"format", "delimiter"}, {"format"})
    if spec["input"]["format"] not in ("json_array", "csv"):
        raise InvalidInput("Only json_array and csv inputs are supported; envelopes are not supported")
    if spec["input"]["format"] == "csv":
        if (not isinstance(spec["input"].get("delimiter"), str)
                or len(spec["input"]["delimiter"]) != 1
                or spec["input"]["delimiter"] in ('\n', '\r', '\x00', '"')):
            raise InvalidInput("CSV requires one explicit supported delimiter")
    elif "delimiter" in spec["input"]:
        raise InvalidInput("Delimiter applies only to CSV")
    keys(spec["source"], {"description", "evidence"}, {"description", "evidence"})
    if not text_nonempty(spec["source"]["description"]) or not evidence(spec["source"]["evidence"]):
        raise InvalidInput("Source description and evidence references are required")
    if not isinstance(spec["assumptions"], list) or not all(text_nonempty(x) for x in spec["assumptions"]):
        raise InvalidInput("Assumptions must be an explicit list of strings")
    keys(spec["scope"], {"kind", "completeness", "evidence"}, {"kind", "completeness", "evidence"})
    if spec["scope"]["kind"] not in ("observed_export_only", "declared_dataset"):
        raise InvalidInput("Unsupported scope kind")
    if spec["scope"]["completeness"] not in ("unverified", "partial", "confirmed"):
        raise InvalidInput("Unsupported completeness declaration")
    if not isinstance(spec["scope"]["evidence"], list) or not all(text_nonempty(x) for x in spec["scope"]["evidence"]):
        raise InvalidInput("Scope evidence must be a list of references")
    if spec["scope"]["completeness"] == "confirmed" and not evidence(spec["scope"]["evidence"]):
        raise InvalidInput("Confirmed completeness requires independent evidence references")
    keys(spec["granularity"], {"description", "contract_status", "evidence"},
         {"description", "contract_status", "evidence"})
    if not text_nonempty(spec["granularity"]["description"]) or spec["granularity"]["contract_status"] not in ("confirmed", "assumed", "unknown"):
        raise InvalidInput("Explicit granularity and its contract status are required")
    if not isinstance(spec["granularity"]["evidence"], list) or not all(text_nonempty(x) for x in spec["granularity"]["evidence"]):
        raise InvalidInput("Granularity evidence must be a list of references")
    if spec["granularity"]["contract_status"] == "confirmed" and not evidence(spec["granularity"]["evidence"]):
        raise InvalidInput("Confirmed granularity requires evidence references")
    keys(spec["fields"], ROLES, ROLES)
    if not all(text_nonempty(x) for x in spec["fields"].values()) or len(set(spec["fields"].values())) != 6:
        raise InvalidInput("Six distinct explicit field mappings are required")
    # A bounded whitelist prevents ambiguous locale, time-zone, week-year and partial date formats.
    if spec["date_format"] not in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        raise InvalidInput("Unsupported date format; use a documented complete numeric calendar format")
    keys(spec["money"], {"currency", "unit"}, {"currency", "unit"})
    if not re.fullmatch(r"[A-Z]{3}", str(spec["money"]["currency"])) or not text_nonempty(spec["money"]["unit"]):
        raise InvalidInput("Explicit currency and one shared input money unit are required")
    keys(spec["period"], {"start", "end", "assert_all_rows_within"},
         {"start", "end", "assert_all_rows_within"})
    start, end = iso_date(spec["period"]["start"]), iso_date(spec["period"]["end"])
    if start > end or (end - start).days > 36600:
        raise InvalidInput("Period is reversed or exceeds 36601 days")
    if not isinstance(spec["period"]["assert_all_rows_within"], bool):
        raise InvalidInput("Period assertion must be a boolean")
    if "include_accounts" in spec:
        a = spec["include_accounts"]
        if not isinstance(a, list) or not all(text_nonempty(x) for x in a) or len(a) != len(set(a)):
            raise InvalidInput("Account filter must be a list of distinct nonempty exact strings")
    keys(spec["contract"], {"gross_minus_returns_equals_net", "receipts", "unique_key"})
    relation = spec["contract"].get("gross_minus_returns_equals_net")
    if relation is not None:
        keys(relation, {"asserted", "tolerance", "evidence"}, {"asserted", "tolerance", "evidence"})
        tolerance, status, _ = parse_number(relation["tolerance"])
        if relation["asserted"] is not True or status != "PASS" or tolerance < 0 or not evidence(relation["evidence"]):
            raise InvalidInput("Relation requires an asserted contract, nonnegative tolerance, and evidence")
    counter = spec["contract"].get("receipts")
    if counter is not None:
        keys(counter, {"integer", "nonnegative", "evidence"}, {"integer", "nonnegative", "evidence"})
        if any(not isinstance(counter[k], bool) for k in ("integer", "nonnegative")) or not evidence(counter["evidence"]):
            raise InvalidInput("Counter constraints require explicit booleans and contract evidence")
    unique = spec["contract"].get("unique_key")
    if unique is not None:
        keys(unique, {"fields", "asserted", "evidence"}, {"fields", "asserted", "evidence"})
        f = unique["fields"]
        if (unique["asserted"] is not True or not evidence(unique["evidence"])
                or not isinstance(f, list) or not f or any(x not in ROLES for x in f)
                or len(f) != len(set(f))):
            raise InvalidInput("Unique key requires an explicit uniqueness contract and distinct logical fields")
    places = spec.get("mean_decimal_places", 6)
    if (isinstance(places, bool) or not isinstance(places, (Decimal, int))
            or not Decimal(places).is_finite() or not 0 <= places <= 24 or places != int(places)):
        raise InvalidInput("Mean decimal places must be an integer between 0 and 24")
    return spec


def load_rows(data, spec):
    if spec["input"]["format"] == "json_array":
        rows = decode_json(data)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise InvalidInput("JSON input must be an array of row objects; envelopes are not supported")
        return rows
    try:
        stream = io.StringIO(data.decode("utf-8-sig"), newline="")
        reader = csv.reader(stream, delimiter=spec["input"]["delimiter"], strict=True)
        header = next(reader, None)
        if header is None or not all(header) or len(header) != len(set(header)):
            raise InvalidInput("CSV requires a nonempty unique header")
        rows = []
        for values in reader:
            if len(values) != len(header):
                raise InvalidInput("CSV row width differs from explicit header")
            rows.append(dict(zip(header, values)))
        return rows
    except (UnicodeError, csv.Error):
        raise InvalidInput("Input is not supported UTF-8 CSV") from None


def check_record(identifier, statuses=(), details=(), expected=None, scope="all_input"):
    counts = {s: 0 for s in STATUSES}
    counts.update(Counter(statuses))
    status = ("FAIL" if counts["FAIL"] else "UNKNOWN" if counts["UNKNOWN"] else
              "PASS" if counts["PASS"] else "NOT_APPLICABLE")
    return {"id": identifier, "status": status, "scope": scope,
            "actual": counts, "expected": expected, "details": list(details)}


def issue(row, status, actual, expected):
    return {"locator": {"row": row}, "status": status, "actual": actual, "expected": expected}


def sum_values(values):
    known = [v for v in values if v is not None]
    unknown = len(values) - len(known)
    total = sum(known, Decimal(0))
    completeness = "empty" if not values else "complete" if not unknown else "partial" if known else "unknown"
    return {"known_sum": decimal_text(total), "total": decimal_text(total) if not unknown else None,
            "known_count": len(known), "unknown_count": unknown, "row_count": len(values),
            "completeness": completeness}


def validate_data(rows, spec, input_sha, spec_sha):
    """Checks are conditioned on the supplied contract; evidence is not authenticated here."""
    checks = []
    normalized = []
    status_lists = defaultdict(list)
    details = defaultdict(list)
    start, end = iso_date(spec["period"]["start"]), iso_date(spec["period"]["end"])
    account_filter = None if "include_accounts" not in spec else set(spec["include_accounts"])
    groups = {g: [] for g in ("all_input", "included", "excluded", "unclassifiable")}
    observed_dates = set()
    missing_filter_accounts = set(account_filter or ())
    period_counts, account_counts = Counter(), Counter()
    for index, row in enumerate(rows, 1):
        parsed = {"row": index}
        for role in NUMERIC:
            value, status, reason = parse_number(row.get(spec["fields"][role], MISSING))
            parsed[role] = value
            key = "numeric_" + role
            status_lists[key].append(status)
            if status != "PASS":
                details[key].append(issue(index, status, {"reason": reason}, "finite decimal in explicit unit"))
        raw_date = row.get(spec["fields"]["date"], MISSING)
        parsed["date"] = None
        if raw_date is MISSING or raw_date is None or raw_date == "":
            dstatus, reason = "UNKNOWN", "missing_or_empty_date"
        else:
            try:
                if not isinstance(raw_date, str):
                    raise ValueError
                d = datetime.strptime(raw_date, spec["date_format"]).date()
                if d.strftime(spec["date_format"]) != raw_date:
                    raise ValueError
                parsed["date"] = d
                observed_dates.add(d)
                dstatus, reason = "PASS", "parsed_calendar_date"
            except (ValueError, TypeError):
                dstatus, reason = "FAIL", "invalid_date_syntax"
        status_lists["date_parse"].append(dstatus)
        if dstatus != "PASS":
            details["date_parse"].append(issue(index, dstatus, {"reason": reason}, spec["date_format"]))
        raw_account = row.get(spec["fields"]["account"], MISSING)
        if raw_account is MISSING or raw_account is None or raw_account == "":
            parsed["account"] = None
            astatus, reason = "UNKNOWN", "missing_or_empty_account"
        elif not text_nonempty(raw_account):
            parsed["account"] = None
            astatus, reason = "FAIL", "account_is_not_nonempty_string"
        else:
            parsed["account"] = raw_account
            astatus, reason = "PASS", "exact_string"
            missing_filter_accounts.discard(raw_account)
        status_lists["account_parse"].append(astatus)
        if astatus != "PASS":
            details["account_parse"].append(issue(index, astatus, {"reason": reason}, "nonempty exact account string"))
        pin = None if parsed["date"] is None else start <= parsed["date"] <= end
        ain = True if account_filter is None else None if parsed["account"] is None else parsed["account"] in account_filter
        period_counts["unknown" if pin is None else "within" if pin else "outside"] += 1
        account_counts["unknown" if ain is None else "included" if ain else "excluded"] += 1
        group = "excluded" if pin is False or ain is False else "included" if pin is True and ain is True else "unclassifiable"
        parsed["classification"] = group
        parsed["period_membership"] = pin
        groups[group].append(parsed)
        groups["all_input"].append(parsed)
        normalized.append(parsed)
        if spec["period"]["assert_all_rows_within"]:
            pstatus = "UNKNOWN" if pin is None else "PASS" if pin else "FAIL"
            status_lists["period_contract"].append(pstatus)
            if pstatus != "PASS":
                details["period_contract"].append(issue(index, pstatus, parsed["date"].isoformat() if parsed["date"] else None,
                                                         {"start": start.isoformat(), "end": end.isoformat()}))
    for key in [*("numeric_" + x for x in NUMERIC), "date_parse", "account_parse", "period_contract"]:
        checks.append(check_record(key, status_lists[key], details[key],
                                   "all rows inside requested period" if key == "period_contract" else "all applicable values conform to explicit schema"))
    relation = spec["contract"].get("gross_minus_returns_equals_net")
    rst, rdetails = [], []
    if relation:
        tolerance = parse_number(relation["tolerance"])[0]
        for row in normalized:
            vals = [row[x] for x in ("gross", "returns", "net")]
            delta = None if any(x is None for x in vals) else vals[0] - vals[1] - vals[2]
            status = "UNKNOWN" if delta is None else "PASS" if abs(delta) <= tolerance else "FAIL"
            rst.append(status)
            if status != "PASS":
                rdetails.append(issue(row["row"], status, {"gross_minus_returns_minus_net": decimal_text(delta)},
                                      {"absolute_delta_at_most": decimal_text(tolerance)}))
    checks.append(check_record("gross_minus_returns_equals_net", rst, rdetails,
                               {"tolerance": relation["tolerance"]} if relation else "not asserted; not tested"))
    counter = spec["contract"].get("receipts", {}) or {}
    for constraint in ("integer", "nonnegative"):
        statuses, ds = [], []
        if counter.get(constraint):
            for row in normalized:
                value = row["receipts"]
                valid = value is not None and (value == value.to_integral_value() if constraint == "integer" else value >= 0)
                status = "UNKNOWN" if value is None else "PASS" if valid else "FAIL"
                statuses.append(status)
                if status != "PASS":
                    ds.append(issue(row["row"], status, decimal_text(value), constraint + " receipt count per asserted contract"))
        checks.append(check_record("receipts_" + constraint, statuses, ds, "contract asserted" if counter.get(constraint) else "not asserted; not tested"))
    unique = spec["contract"].get("unique_key")
    ust, uds = [], []
    if unique:
        seen = {}
        for row in normalized:
            key = tuple(row[x] for x in unique["fields"])
            if any(x is None for x in key):
                ust.append("UNKNOWN")
                uds.append(issue(row["row"], "UNKNOWN", {"reason": "unknown_key_component"}, "complete unique key"))
            elif key in seen:
                ust.append("FAIL")
                uds.append(issue(row["row"], "FAIL", {"same_key_as_row": seen[key]}, "unique key per asserted contract"))
            else:
                seen[key] = row["row"]
                ust.append("PASS")
    checks.append(check_record("unique_key", ust, uds, {"fields": unique["fields"]} if unique else "not asserted; no uniqueness conclusion"))
    # Candidates never remove a row and do not become FAIL without a unique-key contract.
    fingerprints = defaultdict(list)
    for row in normalized:
        key = tuple(row[x] for x in ROLES)
        if all(v is not None for v in key):
            fingerprints[key].append(row["row"])
    candidates = [{"rows": indices, "status": "CANDIDATE_ONLY"} for indices in fingerprints.values() if len(indices) > 1]
    totals = {name: {metric: sum_values([r[metric] for r in group]) for metric in NUMERIC}
              for name, group in groups.items()}
    conservation = []
    parts = ("included", "excluded", "unclassifiable")
    for metric in NUMERIC:
        all_total = totals["all_input"][metric]
        part_known = sum((Decimal(totals[g][metric]["known_sum"]) for g in parts), Decimal(0))
        part_unknown = sum(totals[g][metric]["unknown_count"] for g in parts)
        actual = {"partition_known_sum": decimal_text(part_known), "partition_unknown_count": part_unknown}
        expected = {"partition_known_sum": all_total["known_sum"], "partition_unknown_count": all_total["unknown_count"]}
        conservation.append({"metric": metric, "status": "PASS" if actual == expected else "FAIL", "actual": actual, "expected": expected})
    counts = {g: len(group) for g, group in groups.items()}
    checks.append({"id": "partition_conservation", "status": "PASS" if sum(counts[g] for g in parts) == len(rows) and all(x["status"] == "PASS" for x in conservation) else "FAIL",
                   "scope": "all_input", "actual": {"partition_row_count": sum(counts[g] for g in parts)},
                   "expected": {"partition_row_count": len(rows)}, "details": conservation})
    selected = {}
    ambiguous = counts["unclassifiable"]
    for metric in NUMERIC:
        result = dict(totals["included"][metric])
        result["membership_unknown_rows"] = ambiguous
        if ambiguous:
            result["total"] = None
            result["completeness"] = "partial" if result["known_count"] else "unknown"
        selected[metric] = result
    numerator, denominator = selected["net"], selected["receipts"]
    mean = {"formula": "sum(included net) / sum(included receipts)", "value": None,
            "numerator": numerator["total"], "denominator": denominator["total"],
            "decimal_places": int(spec.get("mean_decimal_places", 6)), "rounding": "ROUND_HALF_EVEN",
            "status": "UNKNOWN", "reason": "incomplete_value_or_membership"}
    if numerator["total"] is not None and denominator["total"] is not None:
        if not counts["included"]:
            mean.update(status="NOT_APPLICABLE", reason="empty_selection")
        elif Decimal(denominator["total"]) == 0:
            mean.update(status="NOT_APPLICABLE", reason="zero_denominator_not_a_business_error")
        else:
            quantum = Decimal(1).scaleb(-mean["decimal_places"])
            value = (Decimal(numerator["total"]) / Decimal(denominator["total"])).quantize(quantum, rounding=ROUND_HALF_EVEN)
            mean.update(status="PASS", reason="computed_from_complete_selected_values", value=decimal_text(value))
    checks.append({"id": "weighted_mean", "status": mean["status"], "scope": "selection",
                   "actual": mean, "expected": "complete selected net and receipts, nonzero denominator", "details": []})
    input_complete = spec["scope"]["completeness"] == "confirmed"
    checks.append({"id": "scope_completeness", "status": "PASS" if input_complete else "UNKNOWN",
                   "scope": spec["scope"]["kind"], "actual": {"kind": spec["scope"]["kind"], "declaration": spec["scope"]["completeness"]},
                   "expected": "evidenced completeness within the expressly declared scope; no inference to the full park", "details": []})
    checks.append({"id": "granularity_contract", "status": "PASS" if spec["granularity"]["contract_status"] == "confirmed" else "UNKNOWN",
                   "scope": "all_input", "actual": spec["granularity"]["contract_status"], "expected": "confirmed granularity contract", "details": []})
    checks.append({"id": "nonempty_selection", "status": "PASS" if counts["included"] else "UNKNOWN",
                   "scope": "selection", "actual": counts["included"], "expected": "at least one included row for a substantive conclusion", "details": []})
    if account_filter is not None:
        checks.append({"id": "requested_accounts_observed", "status": "UNKNOWN" if missing_filter_accounts else "PASS",
                       "scope": "all_input", "actual": {"requested_count": len(account_filter), "not_observed_count": len(missing_filter_accounts)},
                       "expected": "all requested exact account labels observed; absence does not prove zero activity", "details": []})
    arithmetic_checks = [c for c in checks if c["id"] not in ("scope_completeness", "granularity_contract")]
    arithmetic_status = "FAIL" if any(c["status"] == "FAIL" for c in arithmetic_checks) else "UNKNOWN" if any(c["status"] == "UNKNOWN" for c in arithmetic_checks) else "PASS"
    status_counts = dict(Counter(c["status"] for c in checks))
    overall = "FAIL" if status_counts.get("FAIL") else "UNKNOWN" if status_counts.get("UNKNOWN") else "PASS"
    period_dates = {start + timedelta(days=i) for i in range((end-start).days + 1)}
    return {"tool": "check_cashier_data", "version": VERSION, "status": overall,
            "status_scope": spec["scope"]["kind"], "arithmetic_status": arithmetic_status,
            "global_business_status": "NOT_ASSESSED",
            "external_scope_validation": {name: "NOT_TESTED" for name in
                ("full_park_coverage", "employee_identity", "causality", "individual_transactions")},
            "input_sha256": input_sha, "spec_sha256": spec_sha, "row_locator": "one-based JSON array item or CSV data record (header excluded; not physical line)",
            "money": spec["money"], "scope": {"kind": spec["scope"]["kind"], "completeness": spec["scope"]["completeness"], "contract_evidence_authenticated": False},
            "coverage": {"row_counts": counts, "period_membership": dict(sorted(period_counts.items())),
                         "account_membership": dict(sorted(account_counts.items())),
                         "period": {"start": start.isoformat(), "end": end.isoformat()},
                         "observed_dates_within_period": sorted(d.isoformat() for d in observed_dates & period_dates),
                         "dates_without_observed_rows": sorted(d.isoformat() for d in period_dates - observed_dates),
                         "date_presence_proves_business_coverage": False,
                         "row_partition": {g: [r["row"] for r in groups[g]] for g in parts}},
            "totals_by_partition": totals, "selected_totals": selected, "weighted_mean_net_per_receipt": mean,
            "equal_mapped_rows_candidates": candidates, "rows_removed": 0,
            "checks": checks, "check_status_counts": status_counts,
            "limitations": ["Results are conditional on the supplied schema and contract; cited evidence is not authenticated by this CLI.",
                            "Observed aggregates do not prove park-wide coverage or correctness of individual transactions.",
                            "Account labels are not verified people, guests, visits, or employees.",
                            "Missing values and ambiguous membership prevent complete selected totals; no rows are removed.",
                            "Currency and money unit are declared for every monetary field; mixed units cannot be detected or converted.",
                            "A zero receipt denominator makes the ratio inapplicable; returns without new receipts are not automatically business errors."]}


def execute(input_path, spec_path):
    try:
        data, spec_data = input_path.read_bytes(), spec_path.read_bytes()
    except OSError:
        raise InvalidInput("Unable to read input or specification") from None
    spec = validate_spec(decode_json(spec_data, allow_nonfinite=False))
    rows = load_rows(data, spec)
    # Accepted decimals have <=256 significant digits and exponents/magnitudes <=1000.
    # This precision is sufficient for exact sums/differences under those limits.
    with localcontext() as context:
        context.prec = 4096 + len(str(len(rows)))
        result = validate_data(rows, spec, sha(data), sha(spec_data))
    return result, 1 if result["status"] == "FAIL" else 0


def serialize(result):
    # Spec tolerances can originate as JSON numeric literals (Decimal).
    return json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2,
                      allow_nan=False, default=lambda x: decimal_text(x) if isinstance(x, Decimal) else str(x)) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    # Resolve symlinks/hardlinks before writing. Input and spec are strictly read-only.
    try:
        output_resolved = args.output.resolve()
        protected = (args.input, args.spec)
        aliases_input = any(output_resolved == p.resolve() or
                            (args.output.exists() and p.exists() and args.output.samefile(p))
                            for p in protected)
    except (OSError, RuntimeError):
        print("Unable to validate input/output paths", file=sys.stderr)
        return 2
    if aliases_input:
        print("Refusing to overwrite input or specification", file=sys.stderr)
        return 2
    try:
        result, code = execute(args.input, args.spec)
    except InvalidInput as error:
        result = {"tool": "check_cashier_data", "version": VERSION, "status": "INVALID_INPUT", "error": str(error)}
        for label, path in (("input_sha256", args.input), ("spec_sha256", args.spec)):
            try:
                result[label] = sha(path.read_bytes())
            except OSError:
                result[label] = None
        code = 2
    try:
        args.output.write_text(serialize(result), encoding="utf-8")
    except OSError:
        print("Unable to write output", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "exit_code": code}, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
