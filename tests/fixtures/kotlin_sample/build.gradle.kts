plugins {
    kotlin("jvm") version "1.9.22"
    id("com.android.application")
}

dependencies {
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.7.3")
    testImplementation("junit:junit:4.13.2")
    implementation(libs.androidx.core)
}
