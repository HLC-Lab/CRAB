import { createMemoryHistory, createRouter, createWebHistory } from "vue-router";
import { isSbatchmanMode } from "@/lib/mode";

// Lazy-loaded views — one per top-level area of the dashboard.
// Author/Jobs/Results are unhooked here (ADR-026): SbatchMan owns
// run/monitor/results now, and this branch always runs in SbatchMan mode.
// Their view/store/component files are left in the tree, unregistered
// (deferred deletion, see docs/dev/dashboard/deferred.md).
export const routes = [
  { path: "/", redirect: "/sbatchman" },
  {
    path: "/sbatchman",
    name: "sbatchman",
    component: () => import("@/views/SbatchmanView.vue"),
    meta: { title: "SbatchMan" },
    // Reachable only in SbatchMan mode (the served page carries the meta tag,
    // see lib/mode.ts); otherwise the deep link falls back to /remotes (the nav
    // item is hidden too).
    beforeEnter: () => (isSbatchmanMode() ? true : { path: "/remotes" }),
  },
  {
    path: "/remotes",
    name: "remotes",
    component: () => import("@/views/RemotesView.vue"),
    meta: { title: "Remotes" },
  },
  {
    path: "/wrappers",
    name: "wrappers",
    component: () => import("@/views/WrappersView.vue"),
    meta: { title: "Wrappers" },
  },
  // Any other path (/jobs, /author, /results, a typo) lands on the home page.
  // Keep this the LAST route.
  { path: "/:pathMatch(.*)*", redirect: "/sbatchman" },
];

/** Browser tab title for a route: "<meta.title> - CRAB", or "CRAB" without one. */
export function titleFor(route: { meta: Record<string, unknown> }): string {
  const title = route.meta.title;
  return typeof title === "string" && title !== "" ? `${title} - CRAB` : "CRAB";
}

export const router = createRouter({
  // Unit tests import this module in node, where there is no window.
  history: typeof window === "undefined" ? createMemoryHistory() : createWebHistory(),
  routes,
});

router.afterEach((to) => {
  document.title = titleFor(to);
});
