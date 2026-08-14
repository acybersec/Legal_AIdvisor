/** @type {import('next').NextConfig} */
const nextConfig = {
  env: { API_URL: process.env.API_URL ?? "http://127.0.0.1:8000" },
};
export default nextConfig;
