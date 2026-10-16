# Judgment migration fixture

Verdicts saved before limitation-gap verdicts were keyed by their verbatim
source statements (issue #93, ADR 0010 §5): `judgments/gap_judgments.json`
keys every verdict by `gap_id` only, as the dashboard used to write it.
`artifacts/candidate_gaps.json` is the CandidateGaps artifact those verdicts
were made on: one Coverage Gap and two Unanswered Limitation groups.

The tests set the artifact's modification time to before the verdicts, which
is what dates it as the artifact on screen when they were made.
