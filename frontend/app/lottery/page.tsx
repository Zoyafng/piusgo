import Link from 'next/link';
import {serverApi,type Catalog,money} from '@/lib/api';
export const metadata={title:'商品抽奖',robots:{index:false,follow:false}};
export default async function Page(){const catalog=await serverApi<Catalog>('/products');const items=catalog.items.filter(p=>p.product_type==='lottery');return <main id="main" className="site-main"><section className="panel"><h1>商品抽奖</h1><p>选择正在上架的抽奖商品，中奖后免费发放所选规格。</p><div className="product-grid">{items.map(p=><Link key={p.id} href={`/products/${p.id}`} className="product-card lottery-card"><img src={p.image} alt="" width={80} height={80}/><h2>{p.name}</h2><p>奖品价值 {money(p.price)} 起 · 库存 {p.stock}</p><span>查看商品 →</span></Link>)}</div>{!items.length?<p>暂时没有上架的抽奖商品。</p>:null}</section></main>;}
