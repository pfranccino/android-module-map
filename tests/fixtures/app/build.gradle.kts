plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.acme.app"
}

dependencies {
    implementation(project(":feature:login"))
}
