'use client';
export default function ErrorPage({reset}:{reset:()=>void}){return <main id="main" className="site-main"><section className="panel empty"><h1>服务暂时不可用</h1><p>请稍后重试。如果你正在本地预览，请确认前后端服务均已启动。</p><button className="primary" onClick={reset}>重新加载</button></section></main>;}
