import Link from 'next/link';
import {notFound} from 'next/navigation';
import {ApiError,serverApi,type Product} from '@/lib/api';
import Checkout from '@/components/checkout';
import {ArrowLeft} from '@/components/icons';
type Props={params:Promise<{id:string}>};
async function getProduct(id:string){if(!/^\d+$/.test(id))notFound();try{return await serverApi<Product>(`/products/${id}`);}catch(e){if(e instanceof ApiError&&e.status===404)notFound();throw e;}}
export async function generateMetadata({params}:Props){const p=await getProduct((await params).id);return {title:p.name,description:`${p.name}，${p.tags.join('、')}。查看商品详情、价格和交付说明。`,alternates:{canonical:`/products/${p.id}`},openGraph:{title:p.name,images:[p.image]}};}
export default async function ProductPage({params}:Props){const p=await getProduct((await params).id);const base=process.env.SITE_URL||'http://localhost:3000';const schema={'@context':'https://schema.org','@type':'Product',name:p.name,image:base+p.image,description:p.tags.join('、'),offers:{'@type':'Offer',url:`${base}/products/${p.id}`,priceCurrency:'CNY',price:(p.price/100).toFixed(2),availability:p.stock>0?'https://schema.org/InStock':'https://schema.org/OutOfStock'}};return <main id="main" className="site-main"><script type="application/ld+json" dangerouslySetInnerHTML={{__html:JSON.stringify(schema).replace(/</g,'\\u003c')}}/><Link className="back-link" href="/"><ArrowLeft size={17}/>返回商品中心</Link><Checkout product={p}/></main>;}
