import { createRouter, createWebHistory } from "vue-router";

// Lazy-loaded views — one per top-level area of the dashboard.
const routes = [
  { path: "/", redirect: "/remotes" },
  {
    path: "/remotes",
    name: "remotes",
    component: () => import("@/views/RemotesView.vue"),
    meta: { title: "Remotes" },
  },
  {
    path: "/author",
    name: "author",
    component: () => import("@/views/AuthorView.vue"),
    meta: { title: "Author" },
  },
  {
    path: "/wrappers",
    name: "wrappers",
    component: () => import("@/views/WrappersView.vue"),
    meta: { title: "Wrappers" },
  },
  {
    path: "/jobs",
    name: "jobs",
    component: () => import("@/views/JobsView.vue"),
    meta: { title: "Jobs" },
  },
  {
    path: "/results",
    name: "results",
    component: () => import("@/views/ResultsView.vue"),
    meta: { title: "Results" },
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});
