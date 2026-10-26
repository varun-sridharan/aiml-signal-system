"""Step A mutation proofs for the pipeline wiring in agents/weekly.py and agents/repair.py, against the end-to-end gate tests."""
import subprocess, sys, os
TESTS = ["tests.test_weekly.WeeklyRun.test_w1f_style_and_fact_floor_problems_are_warnings_and_never_blocks",
         "tests.test_weekly.WeeklyRun.test_w4f_a_count_that_names_a_held_story_is_removed_and_unlisted",
         "tests.test_weekly.WeeklyRun.test_w5_a_split_that_does_not_reconstruct_is_a_warning_and_the_field_is_graded_whole",
         "tests.test_weekly.WeeklyRun.test_w4e_a_held_story_never_reaches_the_published_brief_in_any_field",
         "tests.test_weekly.WeeklyRun.test_w13a_four_stories_must_pass_or_the_week_does_not_publish_and_says_why"]
M = [
 ("agents/weekly.py", "style warnings become blocks", 'warnings += [f"style — {f}" for f in style_check.edition_style_failures(kept)]', 'hard += [f"style — {f}" for f in style_check.edition_style_failures(kept)]'),
 ("agents/weekly.py", "fact-floor warnings become blocks", "warnings += composer.fact_floor_failures(kept) +", "hard += composer.fact_floor_failures(kept) +"),
 ("agents/weekly.py", "counts are cleaned against the full story list", "repair.drop_failing_counts(cstore, {sid: item for sid, item in ids.items() if sid not in held_sids}, clog)", "repair.drop_failing_counts(cstore, ids, clog)"),
 ("agents/repair.py", "the grader is shown the writer's split even when it hides text", "verdicts, thread_verdict, usage = framer.check_faithfulness(self.client, self.raw_day, self._graded_framing(),", "verdicts, thread_verdict, usage = framer.check_faithfulness(self.client, self.raw_day, self.framing,"),
 ("agents/weekly.py", "the four-story rule is dropped", "if published_n < composer.MIN_STORIES:", "if False:"),
 ("agents/weekly.py", "a held story keeps its grader ledger", "            item[\"faithfulness\"] = {\"held\": True}", "            pass"),
 ("agents/weekly.py", "unsupported claims stay in the stored ledgers", "    out[\"claims\"] = [c for c in (out.get(\"claims\") or []) if c.get(\"supported\")]", "    pass"),
]
out = []
for path, name, old, new in M:
    orig = open(path).read()
    assert old in orig, name
    try:
        open(path, "w").write(orig.replace(old, new, 1))
        r = subprocess.run([sys.executable, "-B", "-m", "unittest", *TESTS], capture_output=True, text=True, env=dict(os.environ, REQUIRE_DB="1", PYTHONDONTWRITEBYTECODE="1"))
    finally:
        open(path, "w").write(orig)
    out.append(f"{'KILLED  ' if r.returncode else 'SURVIVED'} {name}  ({r.stderr.strip().splitlines()[-1]})")
    print(out[-1], flush=True)
r = subprocess.run([sys.executable, "-B", "-m", "unittest", *TESTS], capture_output=True, text=True, env=dict(os.environ, REQUIRE_DB="1", PYTHONDONTWRITEBYTECODE="1"))
print(f"clean code: {r.stderr.strip().splitlines()[-1]}")
