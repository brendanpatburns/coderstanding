import { createRoot } from 'react-dom/client';
import { platformBrowserDynamic } from '@angular/platform-browser-dynamic';
import Dashboard from './ui/Dashboard';
import { AppModule } from './app/app.module';
import { ReportService } from '@/features';

platformBrowserDynamic().bootstrapModule(AppModule);
createRoot(document.body).render(<Dashboard />);
new ReportService().run();
const LazyPage = import('./features/lazy-page');
