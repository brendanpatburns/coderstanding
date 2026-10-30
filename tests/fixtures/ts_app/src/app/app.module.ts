import { NgModule } from '@angular/core';
import { AppComponent } from './app.component';
import { UserService, Auth } from '../services';

@NgModule({
  declarations: [AppComponent],
  providers: [UserService, Auth],
})
export class AppModule {}
