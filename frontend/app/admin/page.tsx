import type {Metadata} from 'next';
import Admin from '@/components/admin';
import './admin.css';
export const metadata:Metadata={title:'管理后台',robots:{index:false,follow:false}};
export default function AdminPage(){return <Admin/>;}
