import { createRouter, createWebHistory } from "vue-router";
import { isSbatchmanMode } from "@/lib/mode";

// Lazy-loaded views — one per top-level area of the dashboard.
// Author/Jobs/Results are unhooked here (plan 085) — SbatchMan owns
// run/monitor/results now, and this branch always runs in SbatchMan mode.
// Their view/store/component files are left in the tree, unregistered
// (deferred deletion, see docs/dev/dashboard/deferred.md).
const routes = [
  { path: "/", redirect: "/sbatchman" },
  {
    path: "/sbatchman",
    name: "sbatchman",
    component: () => import("@/views/SbatchmanView.vue"),
    meta: { title: "SbatchMan" },
    // Only reachable when launched with `crab web --sbatchman`; otherwise the
    // deep link falls back to the default page (the nav item is hidden too).
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
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});
