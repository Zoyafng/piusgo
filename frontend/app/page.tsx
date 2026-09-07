import {serverApi,type Catalog} from '@/lib/api';
import Store from '@/components/store';
export const dynamic='force-dynamic';
export const metadata={alternates:{canonical:'/'}};
export default async function Home(){return <Store catalog={await serverApi<Catalog>('/products')}/>;}
