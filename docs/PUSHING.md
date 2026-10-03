# Pushing Polarizer to GitHub (for the owner, run by hand)

Claude sessions never do any of this: CLAUDE.md rule 11 forbids setting a remote, pushing, creating a GitHub repository or using `gh`.

## 1. Create an empty private repository

1. Open https://github.com/new while signed in.
2. **Owner:** your account. **Repository name:** `polarizer`. **Visibility:** Private.
3. Leave every "Initialize this repository" option off: no README, `.gitignore` None, license None. An empty repository keeps the local history exactly as it is, with no merge needed.
4. Select **Create repository**.

## 2. Add the remote and push `master`

```
cd ~/code/polarizer
git status                  # expect "nothing to commit, working tree clean"
git log --oneline           # the local commits you are about to publish
git remote -v               # expect no output: no remote yet
git remote add origin git@github.com:<your-account>/polarizer.git
git push -u origin master
```

Use `https://github.com/<your-account>/polarizer.git` instead if you push over HTTPS.

**If the push is rejected with `GH007: Your push would publish a private email address`:** your GitHub email privacy setting blocks commits whose author address is your personal one. Either turn that setting off for this push, or tell Claude, which can then explain how to rewrite the local commits to your GitHub no-reply address (that changes every commit hash, so it needs your go-ahead).

The first branch pushed to an empty repository becomes its default, so `master` will be the default branch.

## 3. Check the Actions tab

1. Open the repository, then **Actions**. A run of the workflow **ci** starts on the push.
2. It has five jobs: `ubuntu-latest / Python 3.11`, `3.12` and `3.13`, `windows-latest / Python 3.12` and `macos-latest / Python 3.12`. Every job should end with a green check.
3. In each job, open these steps:
   - **Test**: the last line should read `N passed, M skipped`. On Windows the `test_windows_replace_while_head_is_held_open` test runs; elsewhere it is skipped. The line starting `differential: seed` shows the fuzz seed and case count.
   - **Append benchmark**: two lines, `write only` and `write + fsync`, with p50 and p95. These numbers go into docs/verified-facts.md, so paste them back even when everything passes.
4. Private repositories spend Actions minutes, and Windows and macOS minutes count at a higher rate than Linux.

## 4. What to paste back

**If every job passes:** reply `CI green`, followed by the two benchmark lines from each of the five jobs.

**If a job fails,** paste for each failing job:
- the job name, for example `windows-latest / Python 3.12`;
- the name of the failing step (`Install`, `Test` or `Append benchmark`);
- that step's log from the first error to the end. For pytest, that is each failure's traceback and the `short test summary info` block. Include the `differential: seed` line if `test_differential.py` failed;
- for a failure in `Install`, the whole step log.

If the log is long, use the job's **Download log archive** and paste only the failing step. The workflow uses no secrets, so the logs hold none.
