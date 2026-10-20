import { createApp } from "vue";
import { createPinia } from "pinia";
import { router } from "@/router";
import { useAppStore } from "@/stores/app";
import App from "@/App.vue";
import "@/styles/tokens.css";

const app = createApp(App);
app.use(createPinia());
app.use(router);

// Apply the persisted theme before mount to avoid a flash.
useAppStore().applyTheme();

app.mount("#app");
