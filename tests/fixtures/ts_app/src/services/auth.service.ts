import { inject, Injectable } from '@angular/core';
import { ApiService } from './api.service.js';

@Injectable()
export class AuthService {
  private api = inject(ApiService);
}
