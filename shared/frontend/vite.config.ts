import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const CHAT_LAMBDA_URL = 'https://szfoqv25uftblx6xpowrslzi3y0yumcy.lambda-url.us-east-1.on.aws'
const COMPANY_LAMBDA_URL = 'https://5ke5e7f2ofyxonkh62groo7s7i0zunmq.lambda-url.us-east-1.on.aws'

export default defineConfig({
  plugins: [
    tailwindcss(),
    react(),
  ],
  server: {
    proxy: {
      '/chat-proxy': {
        target: CHAT_LAMBDA_URL,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/chat-proxy/, ''),
      },
      '/company-proxy': {
        target: COMPANY_LAMBDA_URL,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/company-proxy/, ''),
      },
    },
  },
})
