/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Refined Binance/professional dark palette
        bg: {
          DEFAULT: '#0b0e11',          // Binance true black-ish
          card:    '#1e2329',          // raised surface
          hover:   '#2b3139',
          border:  '#2b3139',
          subtle:  '#161a1f',
        },
        accent: {
          green:  '#0ecb81',           // Binance up green
          red:    '#f6465d',           // Binance down red
          blue:   '#1abcfe',
          orange: '#f0b90b',           // Binance gold-orange
          purple: '#a855f7',
          yellow: '#fcd535',
        },
        text: {
          DEFAULT: '#eaecef',
          muted:   '#848e9c',
          dim:     '#5e6673',
          bright:  '#f5f5f5',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'SF Mono', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': '0.625rem',  // 10px
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in':    'fadeIn 0.2s ease-out',
        'slide-in':   'slideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
        'flash-green': 'flashGreen 0.6s ease-out',
        'flash-red':   'flashRed 0.6s ease-out',
      },
      keyframes: {
        fadeIn:  { '0%': { opacity: 0 }, '100%': { opacity: 1 } },
        slideIn: { '0%': { transform: 'translateX(-100%)' }, '100%': { transform: 'translateX(0)' } },
        flashGreen: {
          '0%':   { backgroundColor: 'rgba(14, 203, 129, 0.25)' },
          '100%': { backgroundColor: 'transparent' },
        },
        flashRed: {
          '0%':   { backgroundColor: 'rgba(246, 70, 93, 0.25)' },
          '100%': { backgroundColor: 'transparent' },
        },
      },
      boxShadow: {
        glow: '0 0 20px rgba(26, 188, 254, 0.15)',
      },
    },
  },
  plugins: [],
};
