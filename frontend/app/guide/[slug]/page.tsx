import Link from 'next/link';
import {notFound} from 'next/navigation';
import {ApiError,serverApi} from '@/lib/api';
type Content={title:string;intro:string;sections:{title:string;text:string}[]};
type Props={params:Promise<{slug:string}>};
async function load(slug:string){try{return await serverApi<Content>(`/pages/${encodeURIComponent(slug)}`);}catch(e){if(e instanceof ApiError&&e.status===404)notFound();throw e;}}
export async function generateMetadata({params}:Props){const c=await load((await params).slug);return {title:c.title,robots:{index:false,follow:false}};}
export default async function Guide({params}:Props){const c=await load((await params).slug);return <main id="main" className="site-main"><article className="panel prose"><span className="eyebrow">HELP CENTER</span><h1>{c.title}</h1><p>{c.intro}</p>{c.sections.map((s,i)=><section key={i}><h2>{s.title}</h2><p style={{whiteSpace:'pre-wrap'}}>{s.text}</p></section>)}<Link className="secondary" href="/tickets">提交工单</Link></article></main>;}
