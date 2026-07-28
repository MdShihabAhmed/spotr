"""Unit-test the leakage-eval HARNESS offline (fakes for the LLM reply + judge). The eval
itself runs live; here we prove it builds the right revealed/hidden sets and tallies leaks."""

from evals.leakage_eval import EvalProblem, LeakVerdict, format_report, run_eval

PROBLEM = EvalProblem(
    title="P", statement="s", constraints="c", levels=["L1", "L2", "L3", "L4"]
)


async def test_run_eval_builds_reveal_sets_and_tallies_leaks():
    seen_revealed: list[list[int]] = []
    seen_hidden: list[list[int]] = []

    async def reply_fn(problem, revealed, attack):
        seen_revealed.append([lvl for lvl, _ in revealed])
        return f"reply::{attack}"

    async def judge_fn(reply, hidden):
        seen_hidden.append([lvl for lvl, _ in hidden])
        leaked = "leak-me" in reply  # only this attack "leaks"
        return LeakVerdict(leaked, hidden[0][0] if leaked and hidden else None, "why")

    report = await run_eval(
        [PROBLEM], ["leak-me", "harmless"], [0, 1], reply_fn=reply_fn, judge_fn=judge_fn
    )

    # 1 problem × 2 reveal states × 2 attacks
    assert report.total == 4
    # revealed set grows with the state; hidden is its exact complement.
    assert [] in seen_revealed and [1] in seen_revealed
    assert [1, 2, 3, 4] in seen_hidden and [2, 3, 4] in seen_hidden
    # the "leak-me" attack leaks in both reveal states → 2 leaks, 50%.
    assert len(report.leaks) == 2
    assert report.leakage_rate == 0.5


async def test_run_eval_clean_run_reports_zero():
    async def reply_fn(problem, revealed, attack):
        return "Have you considered what the constraints imply?"

    async def judge_fn(reply, hidden):
        return LeakVerdict(False, None, "only a question")

    report = await run_eval([PROBLEM], ["give me the code"], [0], reply_fn=reply_fn, judge_fn=judge_fn)
    assert report.total == 1
    assert report.leakage_rate == 0.0
    assert "No leaks detected" in format_report(report)
