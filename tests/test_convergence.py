"""
Local-only tests for convergence utils (src/crab/core/data/utils.py) critical issues.
"""

import pytest

from crab.core.data.containers import DataContainer
from crab.core.data.utils import check_CI


def _container(conv_goal=True, data=None):
    c = DataContainer(0, conv_goal, "metric", "s")
    if data is not None:
        c.data = list(data)
        c.num_samples = [1] * len(c.data)
    return c


# ---------------------------------------------------------------------------
# Issue 4: Convergence criterion broken for negative or zero means
# ---------------------------------------------------------------------------


class TestNegativeMeanConvergence:
    def test_negative_mean_converges_when_ci_is_tight(self):
        """A metric with a negative mean should converge when CI width < beta * |mean|."""
        c = _container(conv_goal=True, data=[-100.0] * 19 + [-100.001])
        # mean ≈ -100, very small sem → CI width << beta * |mean| = 5.0
        result = check_CI([c], alpha=0.05, beta=0.05, converge_all=False, run=20)
        assert result is True, "Negative mean should converge — bug: beta * mean is negative"
        assert c.converged is True

    def test_negative_mean_not_spuriously_converged_by_wide_ci(self):
        """When CI is wide relative to |mean|, it must NOT converge even for negative mean."""
        c = _container(conv_goal=True, data=[-1.0, -100.0])  # huge spread, n=2
        result = check_CI([c], alpha=0.05, beta=0.05, converge_all=False, run=2)
        assert result is False, "Wide CI with negative mean should not converge"

    def test_zero_mean_does_not_raise(self):
        """Zero mean must not raise an exception (divide-by-zero guard)."""
        c = _container(conv_goal=True, data=[0.0] * 10 + [0.001])
        try:
            check_CI([c], alpha=0.05, beta=0.05, converge_all=False, run=11)
        except Exception as e:
            pytest.fail(f"check_CI raised unexpectedly for zero-mean data: {e}")


# ---------------------------------------------------------------------------
# Issue 5: check_CI returns True when no container has conv_goal=True
# ---------------------------------------------------------------------------


class TestNoConvGoal:
    def test_no_conv_goal_container_returns_false(self):
        """When no container has conv_goal=True and converge_all=False, must return False."""
        c = _container(conv_goal=False, data=[1.0] * 10)
        result = check_CI([c], alpha=0.05, beta=0.05, converge_all=False, run=10)
        assert result is False, (
            "check_CI returned True with no conv_goal targets — "
            "experiment would terminate with zero statistical evaluation"
        )

    def test_empty_container_list_returns_false(self):
        """Empty container list must return False, not True."""
        result = check_CI([], alpha=0.05, beta=0.05, converge_all=False, run=1)
        assert result is False, "Empty container list must not be treated as converged"

    def test_conv_goal_true_still_works(self):
        """Normal path: conv_goal=True container that has converged must return True."""
        c = _container(conv_goal=True, data=[10.0] * 19 + [10.001])
        # mean ≈ 10, very small sem → CI width << beta * mean = 0.5
        result = check_CI([c], alpha=0.05, beta=0.05, converge_all=False, run=20)
        assert result is True

    def test_mixed_goal_requires_all_goal_containers(self):
        """With two conv_goal containers, both must be converged to return True."""
        c1 = _container(conv_goal=True, data=[10.0] * 19 + [10.001])  # tight CI, converges
        c2 = _container(conv_goal=True, data=[10.0, 100.0])  # wide CI, does not
        result = check_CI([c1, c2], alpha=0.05, beta=0.05, converge_all=False, run=2)
        assert result is False
