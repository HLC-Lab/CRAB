"""On the sbatchman branch, the dashboard's "Install CRAB" must install this branch.

The SbatchMan guide (docs/using/sbatchman-integration.md) tells partners to run the
`sbatchman` branch on the cluster; the one-click bootstrap has to agree with it.
"""

from crab.web.remoteops.bootstrap import default_plan
from crab.web.store.profiles import Profile


def test_bootstrap_clones_the_sbatchman_branch():
    profile = Profile(
        name="c", transport="ssh", host="h", user="u", hostkey_policy="insecure", remote_crab="~"
    )
    clone = next(s for s in default_plan(profile) if s.id == "clone")
    assert "--branch sbatchman " in clone.command
