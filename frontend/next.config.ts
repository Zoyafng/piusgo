import type { NextConfig } from 'next';
const config: NextConfig = {
  poweredByHeader: false,
  logging: { incomingRequests: false },
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${process.env.API_URL || 'http://127.0.0.1:8000'}/api/:path*` }];
  },
  async redirects() {
    return [
      {source:'/login.html',destination:'/login',permanent:true},
      {source:'/orders.html',destination:'/orders',permanent:true},
      {source:'/member.html',destination:'/account',permanent:true},
    ];
  },
  async headers() {
    return [{source:'/:path*',headers:[{key:'X-Content-Type-Options',value:'nosniff'},{key:'Referrer-Policy',value:'no-referrer'},{key:'X-Frame-Options',value:'DENY'}]}];
  },
};
export default config;
