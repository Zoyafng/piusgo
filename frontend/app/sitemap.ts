import type {MetadataRoute} from 'next';
import {serverApi,type Catalog} from '@/lib/api';
export default async function sitemap():Promise<MetadataRoute.Sitemap>{if(process.env.INDEX_SITE!=='true')return [];const base=process.env.SITE_URL||'http://localhost:3000';const c=await serverApi<Catalog>('/products');return [{url:base,changeFrequency:'daily',priority:1},...c.items.map(p=>({url:`${base}/products/${p.id}`,changeFrequency:'daily' as const,priority:.8}))];}
