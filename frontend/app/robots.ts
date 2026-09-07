import type {MetadataRoute} from 'next';
export default function robots():MetadataRoute.Robots{const enabled=process.env.INDEX_SITE==='true';return {rules:{userAgent:'*',allow:enabled?'/':undefined,disallow:enabled?['/api/','/login','/account','/orders','/tickets','/offers/','/lottery']:['/']},sitemap:enabled?`${process.env.SITE_URL||'http://localhost:3000'}/sitemap.xml`:undefined};}
