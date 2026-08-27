"""Deterministic analysis over a snapshot.

Every function here is pure: values in, values out, no I/O and no framework
imports. Rules call them; the maths is testable on its own, and a rule that
wants to explain itself can quote the numbers rather than a verdict.
"""
