#!/usr/bin/env python3
"""S365 soak-end fixtures — 6 FP + 6 TP from 2026-05-19 audit."""
import importlib.util, sys, unittest
from pathlib import Path
_s = importlib.util.spec_from_file_location("cps", Path(__file__).parent / "claim-provenance-sentinel.py")
cps = importlib.util.module_from_spec(_s); _s.loader.exec_module(cps)
D = {d['id']: d['pattern'] for d in cps.CLAIM_DETECTORS}
FP=[('hooks_count','4 hook quotes ready in source file'),
('subscription_row_claim',"deposited $5 voucher unlocks"),
('subscription_row_claim',"SKIPPED for $0 per plan"),
('subscription_row_claim',"build the $7 skill that runs"),
('subscription_row_claim',"envelope $0.60-1.20 upgraded"),
('subscription_row_claim',"UI ~2-5 min each rename"),]
TP=[('subscription_row_claim',"qwen3-tts.org for $13/mo voice"),
('subscription_row_claim',"Claude Code $200 kie.ai $30 stack"),
('subscription_row_claim',"upgrade $80 next quarter"),
('pending_count',"Phase 3 PENDING Phase 4 PENDING"),
('hooks_count',"58 hooks shipped to .claude/hooks today"),
('revenue_dollar_claim',"5 tools About $316/mo total stack"),]

class TestSoakEndFP(unittest.TestCase):
    def test_all_fp_dropped(self):
        for det, txt in FP:
            self.assertIsNone(D[det].search(txt), f"FP REGRESSION: {det} fired on {txt!r}")

class TestSoakEndTP(unittest.TestCase):
    def test_all_tp_fire(self):
        for det, txt in TP:
            self.assertIsNotNone(D[det].search(txt), f"TP LOSS: {det} on {txt!r}")
