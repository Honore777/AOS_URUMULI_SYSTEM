/** @type {import('tailwindcss').Config} */
const colors = require('tailwindcss/colors')

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./static/js/**/*.js",
    "./static/css/**/*.css"
  ],
  theme: {
    extend: {
      colors: {
        // Ensure these palettes are available for explicit classes used in templates
        slate: colors.slate,
        emerald: colors.emerald,
        rose: colors.rose,
      },
    },
  },
  plugins: [],
};
