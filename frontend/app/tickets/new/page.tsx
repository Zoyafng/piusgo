import {Suspense} from 'react';
import TicketCreate from '@/components/ticket-create';
export const metadata={title:'创建工单',robots:{index:false,follow:false}};
export default function Page(){return <Suspense fallback={<main id="main" className="site-main loading">正在加载…</main>}><TicketCreate/></Suspense>;}
