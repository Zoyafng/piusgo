import {serverApi,type Catalog,type Lottery} from '@/lib/api';
import Store from '@/components/store';
export const dynamic='force-dynamic';
export const metadata={alternates:{canonical:'/'}};
export default async function Home() {
  const [catalog,lottery]=await Promise.all([serverApi<Catalog>('/products'),serverApi<Lottery>('/lottery')]);
  return <Store catalog={catalog} lottery={lottery}/>;
}
