import nextCoreVitals from "eslint-config-next/core-web-vitals";

const config = [
  ...nextCoreVitals,
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
];

export default config;
