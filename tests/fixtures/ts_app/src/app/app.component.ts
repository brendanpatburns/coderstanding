import { Component } from '@angular/core';
import { UserService } from '@services/index';
import { UserCardComponent } from './user-card.component';

@Component({
  selector: 'app-root',
  imports: [UserCardComponent],
  template: '<app-user-card></app-user-card>',
})
export class AppComponent {
  constructor(private readonly users: UserService) {}

  load(): void {
    this.users.refresh();
  }
}
