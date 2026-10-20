import pluginVue from "eslint-plugin-vue";
import { defineConfigWithVueTs, vueTsConfigs } from "@vue/eslint-config-typescript";
import skipFormatting from "@vue/eslint-config-prettier/skip-formatting";

// Lint catches bugs; Prettier owns formatting (skipFormatting disables all
// stylistic rules so the two never fight).
export default defineConfigWithVueTs(
  { files: ["**/*.{ts,mts,tsx,vue}"] },
  { ignores: ["dist/**", "node_modules/**", "../web/static/**", "tests/e2e/**"] },
  pluginVue.configs["flat/essential"],
  vueTsConfigs.recommended,
  skipFormatting,
  {
    rules: {
      // The config model deliberately uses draft mutation + v-model on props' inner
      // fields; keep the essential ruleset but allow the established patterns.
      "vue/multi-word-component-names": "off",
      "@typescript-eslint/no-explicit-any": "warn",
    },
  },
);
