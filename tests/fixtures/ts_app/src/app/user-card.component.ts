import { Component, Input } from '@angular/core';
import type { User } from '@/models';

@Component({ selector: 'app-user-card', template: '' })
export class UserCardComponent {
  @Input() user?: User;
}
