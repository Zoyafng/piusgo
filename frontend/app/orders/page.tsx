import {Suspense} from 'react';
import OrderLookup from '@/components/order-lookup';
export const metadata={title:'订单查询',robots:{index:false,follow:false}};
export default function Page(){return <Suspense fallback={<main id="main" className="site-main loading">正在加载订单查询…</main>}><OrderLookup/></Suspense>;}
