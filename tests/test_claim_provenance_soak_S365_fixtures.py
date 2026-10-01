#!/usr/bin/env python3
"""S365 soak-end fixtures — 6 FP + 6 TP from 2026-05-19 audit.

The `subscription_row_claim` detector these fixtures were written against was
retired in S383 (see the comment in CLAIM_DETECTORS in
hooks/claim-provenance-sentinel.py). The rows that targeted it are kept and
re-pointed at what the shipped hook actually does now:

  * FP rows: no live detector may fire on them (a stricter check than before,
    which only checked the one detector).
  * TP rows: the retired detector must be absent from the registry. The one
    row a surviving detector still catches ("$13/mo") is asserted against that
    detector. The other two ("$200 ... $30 stack", "$80 next quarter") are
    knowingly undetected since the S383 retirement; that coverage loss is
    the hook's intended behaviour, asserted explicitly so a silent change shows.
"""
import importlib.util, sys, unittest
from pathlib import Path
_s = importlib.util.spec_from_file_location("cps", Path(__file__).resolve().parent.parent / "hooks" / "claim-provenance-sentinel.py")
cps = importlib.util.module_from_spec(_s); _s.loader.exec_module(cps)
D = {d['id']: d['pattern'] for d in cps.CLAIM_DETECTORS}
RETIRED = 'subscription_row_claim'

FP=[('hooks_count','4 hook quotes ready in source file'),
(RETIRED,"deposited $5 voucher unlocks"),
(RETIRED,"SKIPPED for $0 per plan"),
(RETIRED,"build the $7 skill that runs"),
(RETIRED,"envelope $0.60-1.20 upgraded"),
(RETIRED,"UI ~2-5 min each rename"),]
TP=[('revenue_dollar_claim',"qwen3-tts.org for $13/mo voice"),  # was subscription_row_claim; still caught here
('pending_count',"Phase 3 PENDING Phase 4 PENDING"),
('hooks_count',"58 hooks shipped to .claude/hooks today"),
('revenue_dollar_claim',"5 tools About $316/mo total stack"),]
# Former subscription_row_claim TPs that no surviving detector catches.
RETIRED_TP=["Claude Code $200 kie.ai $30 stack",
"upgrade $80 next quarter",]


def _live_hits(txt):
    return [i for i, p in D.items() if p.search(txt)]


class TestSoakEndFP(unittest.TestCase):
    def test_all_fp_dropped(self):
        for det, txt in FP:
            if det == RETIRED:
                self.assertEqual(_live_hits(txt), [], f"FP REGRESSION: live detector fired on {txt!r}")
            else:
                self.assertIsNone(D[det].search(txt), f"FP REGRESSION: {det} fired on {txt!r}")

class TestSoakEndTP(unittest.TestCase):
    def test_all_tp_fire(self):
        for det, txt in TP:
            self.assertIsNotNone(D[det].search(txt), f"TP LOSS: {det} on {txt!r}")

    def test_subscription_row_claim_retired(self):
        self.assertNotIn(RETIRED, D, "subscription_row_claim was retired in S383; it is back in the registry")
        for txt in RETIRED_TP:
            self.assertEqual(_live_hits(txt), [],
                             f"{txt!r} is now caught by a live detector; promote it back into TP")
