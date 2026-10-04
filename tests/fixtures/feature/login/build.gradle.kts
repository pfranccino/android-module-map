plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
    id("com.google.dagger.hilt.android")
}

android {
    namespace = "com.acme.login"
}

dependencies {
    implementation(project(":core:network"))
    api(project(":core:model"))
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    testImplementation("junit:junit:4.13.2")
}
