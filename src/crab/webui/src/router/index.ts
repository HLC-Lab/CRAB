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
    path: "/jobs/report/:configName",
    name: "job-report",
    component: () => import("@/views/ReportView.vue"),
    meta: { title: "Use case report" },
  },
  {
    path: "/jobs/:recordId",
    name: "job-detail",
    component: () => import("@/views/JobDetailView.vue"),
    meta: { title: "Job detail" },
  },
  {
    path: "/results",
    name: "results",
    component: () => import("@/views/ResultsView.vue"),
    meta: { title: "Results" },
  },
  {
    path: "/results/compare",
    name: "results-compare",
    component: () => import("@/views/ResultsCompareView.vue"),
    meta: { title: "Compare results" },
  },
  {
    path: "/results/:cluster/:system/:jobBasename",
    name: "results-job",
    component: () => import("@/views/ResultsJobView.vue"),
    meta: { title: "Results" },
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});
