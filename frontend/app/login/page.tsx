import {Suspense} from 'react';
import Auth from '@/components/auth';
export const metadata={title:'登录',robots:{index:false,follow:false}};
export default function Login(){return <main id="main" className="site-main auth-page"><Suspense fallback={<div className="auth-card">正在加载登录表单…</div>}><Auth/></Suspense></main>;}
