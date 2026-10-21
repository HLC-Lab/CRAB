# Wrappers in the web dashboard

The **Wrappers** page of the web dashboard (`crab web`, see [Installation](installation.md)) shows
which [wrappers](../glossary.md#wrapper) CRAB can see on this laptop and on each cluster you
connect, whether their copy is current, and what each one measures. It does not edit wrappers: you
write them in your editor (see [Writing a new wrapper](#writing-a-new-wrapper)).

Every check on this page runs the `crab` command that is installed on the machine in question, so
the dashboard shows what a run there would see. The equivalent commands are listed at the end.

## Targets

A target is a place the dashboard looks at wrappers (see the [glossary](../glossary.md#target)):

- **This laptop** is the machine running `crab web`. It is always listed and needs no connection.
- Each cluster profile from the **Remotes** page is a target too. A cluster is listed before you
  connect to it, but its wrappers show only once it is connected.

Each target has its own crab-wrappers clone (the folder `crab update` manages) and its own
[receipts](../glossary.md#receipt), so the same wrapper can be fine on one target and broken on
another.

## Status

The **Status** tab has one row per target:

| Column | Shows |
|---|---|
| Target | The name, and below it the wrappers folder (with `over ssh` for a cluster). A cluster that is not connected says `not connected`, with a **Connect** button (a cluster that logs in with a password says **Connect in Remotes** instead). |
| crab-wrappers | The short commit id and the subject line of the clone's latest commit. |
| GitHub | How the clone compares with [HLC-Lab/crab-wrappers](https://github.com/HLC-Lab/crab-wrappers) on GitHub. |
| Wrappers | How many wrappers the target has, and `N need attention` when some do. |

The GitHub column says one of these:

| Text | Meaning and what to do |
|---|---|
| `up to date` | Nothing to do. |
| `N commits behind` | GitHub has commits the clone lacks. The button **Pull N commits** fast-forwards the clone. |
| `behind GitHub` | The same, but GitHub's commits have not been fetched yet, so there is no count. **Pull** fetches them. |
| `N modified files` | Tracked files in the clone were edited, which blocks a pull. Commit, stash or move the edits to `local/wrappers`, then Pull. When the clone is also behind, both lines show and the disabled **Pull** says `commit, stash or move the edits first`. |
| `not cloned yet` | The wrappers folder does not exist on that target. **Clone** clones it. |
| `ahead of GitHub`, `diverged from GitHub` | The clone has commits GitHub lacks (diverged: GitHub has others too). There is no button: resolve it in the clone with git. |
| `GitHub not reachable`, `unknown` | GitHub could not be asked, or there is nothing to compare. This is normal offline. |
| `not a git clone` | The wrappers folder is not a git clone (for example one you copied or pointed `CRAB_PATH_WRAPPERS` at), so there is nothing to pull. |

Pull reports what it did under the row: `Pulled <old> to <new>`, `Already up to date`, or
`Cloned at <commit>`. A pull that fails (edits in the way, a diverged clone) shows the error there
and changes nothing. The dashboard checks GitHub when you open the page; the line under the table
says when, and **Check again** repeats it. If a cluster's `crab` is too old for a command, the row
says `Update crab on <cluster> (crab update) to see this.`

### Needs attention

Click a connected row to select it (the arrow marks the selected one). Below the table the page
lists what is wrong with that target's wrappers, or says `Nothing needs attention on <target>`.
The page starts on the first target that has a problem, else on This laptop.

**Cannot load.** The wrapper's Python file raises an error when CRAB imports it. The first line of
the error is shown, and **Open in Library** shows all of it. The error comes from the wrapper
itself, so the fix is in your editor.

**Binary missing.** The wrapper loads but CRAB found no binary for it: no receipt, no entry in the
config and nothing on `PATH` under the executable's name. What to do depends on the wrapper:

- When the wrapper names a receipt id, **Import binary** opens a form: the binary's path on that
  target, the launcher (`cluster default`, `srun` or `mpirun`) and optional commands to run first.
  **Save receipt** writes the receipt on that target, so every run there uses the binary. Receipts
  are per target: importing on one cluster does not change another.
- A wrapper without a receipt id cannot take an imported binary. Set the app's `binary` in the
  experiment config instead (see [Finding the binary](../extending/wrappers.md#finding-the-binary)).

**Lookup failed.** A grey line, not counted as a problem: `N wrappers could not look up their
binary`, with the reason in parentheses and **Show them** for the list. The lookup itself raised
an error, which is normal on a laptop when a wrapper needs something only a cluster has (for
example `CRAB_SYSTEM`). A receipt would not fix it, so there is no **Import binary** for these.

## Library

The **Library** tab browses one target's wrappers. **Showing** (top right) picks the target,
among the connected ones. Switching target keeps the selected wrapper when the new target has it
too, so you can compare the same wrapper on two machines.

On the left, a tree of the wrappers by folder, with a **Filter wrappers** box (it matches any part
of the path). Folders show their wrapper count when closed, and a folder with more than six files
shows `+ N more`. A section headed `local` comes first when the target has wrappers in
`local/wrappers`, the untracked folder searched before everything else (see
[Where CRAB looks for wrappers](../extending/wrappers.md#where-crab-looks-for-wrappers)). A
wrapper that needs attention is marked `cannot load` or `no binary`.

On the right, the summary of the selected wrapper. It is named after its file, without `.py`, and
shows:

- The path, and where the file came from: `view on GitHub at <commit>` links to the file at
  the clone's commit. A wrapper in `local/wrappers` says `only in this checkout`, and any other
  wrapper without a GitHub link (for example one in a folder that is not a clone of a GitHub
  repository) says `not on GitHub`.
- **Cannot load**, with the full error, when the wrapper does not import.
- The description. It is the wrapper file's docstring. When there is none, it is the first
  paragraph of the nearest `README.md` below the wrappers folder, with a line saying so. When
  there is neither, a line says there is no description.
- **What it measures.** The metrics the wrapper declares, with their unit. `convergence target`
  marks a metric an experiment waits on (see [Convergence](../glossary.md#convergence)), and
  `check` marks a pass or fail verdict on the run (see
  [Correctness checks](../extending/wrappers.md#correctness-checks)). A wrapper with none says
  `This wrapper declares no metrics.`
- **How it runs.** The wrapper's executable (or `not declared`) and where its binary comes from on
  the shown target: `found, set in config`, `found, receipt`, `found on PATH`, `missing on <target>`
  with the reason, or `could not look up the binary here` with the reason. **Import binary**
  appears here too, under the same conditions as in Status.

A wrapper that was deleted from the target while the page was open reports that it is no longer
there. A target with no wrappers says so, and the Status tab can clone or pull them.

## Writing a new wrapper

The dashboard does not write wrapper files. **New wrapper** (top right of the Library) shows the
command that creates them, with a **Copy** button:

```bash
crab wrappers new myapp --local
```

Run it in a terminal on this laptop. With `--local` the wrapper goes to `local/wrappers/myapp/`,
which only exists in this checkout. Without it, the wrapper goes to your crab-wrappers clone,
ready for a pull request to
[HLC-Lab/crab-wrappers](https://github.com/HLC-Lab/crab-wrappers). Then edit the files in your
editor and reload the page: the wrapper shows up in the Library. [Writing a
wrapper](../extending/wrappers.md) covers the contract, the samples and `crab wrappers test`.

## From the experiment editor

In the **Author** page, each app of an experiment has an **About this wrapper** link under its
wrapper path. It opens the Library on that wrapper: on the cluster named in `Wrappers from <cluster>`
when that cluster has the wrapper, and on This laptop otherwise.

## The same from the command line

| In the dashboard | Command |
|---|---|
| Status row | [`crab wrappers status`](../reference/cli.md#crab-wrappers) |
| Pull, Clone | [`crab wrappers pull`](../reference/cli.md#crab-wrappers) |
| Library tree | [`crab wrappers list`](../reference/cli.md#crab-wrappers) |
| Library summary | [`crab wrappers show`](../reference/cli.md#crab-wrappers) |
| Save receipt | [`crab receipts set`](../reference/cli.md#crab-receipts-set) |
| New wrapper | [`crab wrappers new`](../reference/cli.md#crab-wrappers) |
