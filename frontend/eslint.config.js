import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

/**
 Styling discipline (AGENTS.md + frontend skill): CSS Modules only.
 Guards:
  - no inline `style` attributes (except a custom-property-only object, the
    documented escape hatch for data-derived values)
  - no Mantine visual style props (mt/p/c/bg/fz/w/…) — if it draws, it
    belongs in the component's *.module.css
*/
const MANTINE_STYLE_PROPS = [
  "m", "mx", "my", "mt", "mb", "ml", "mr",
  "p", "px", "py", "pt", "pb", "pl", "pr",
  "bg", "c", "color", "opacity", "fz", "fw", "ff", "lh", "ta", "td", "tt",
  "w", "minW", "maxW", "h", "minH", "maxH", "miw", "mih", "maw", "mah",
  "bgt", "left", "top", "bottom", "right", "inset", "pos", "display", "flex",
  "radius", "underline", "lineClamp", "visibleFrom", "hiddenFrom",
].map((prop) => ({ prop, message: `style the component in its *.module.css instead` }));

export default tseslint.config(
  { ignores: ["dist", "src/api/schema.d.ts", "scripts/openapi.json"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    languageOptions: {
      ecmaVersion: 2022,
      globals: {
        window: "readonly",
        document: "readonly",
        localStorage: "readonly",
        fetch: "readonly",
        console: "readonly",
        process: "readonly",
      },
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": "off",
      "@typescript-eslint/no-explicit-any": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='style']",
          message:
            "Inline style props are forbidden — use the component's *.module.css. Only exception: data-derived CSS custom properties (see frontend skill).",
        },
        ...MANTINE_STYLE_PROPS.map(({ prop, message }) => ({
          selector: `JSXAttribute[name.name='${prop}']`,
          message: `${prop}= is a visual style prop: ${message}.`,
        })),
      ],
    },
  },
);
